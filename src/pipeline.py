"""Chronological replay of the static Online Retail II monthly partitions.

The validated full SQL load is the bootstrap. This CLI adds period sequencing,
idempotent period work, quality gates, and a success-only watermark. Only
explicit ``init`` and ``run`` commands may mutate SQL Server.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pyodbc

from .config import PROJECT_ROOT, TRANSACTIONS_PARQUET_DIR
from .db import SqlServerSettings, connect, verify_connection
from .load_staging import (
    SOURCE_ROW_ID_PATTERN,
    _assert_physical_schema,
    load_accepted_parquet,
)


PIPELINE_NAME = "retail_monthly_replay"
APPROVED_SOURCE_MIN = date(2009, 12, 1)
APPROVED_SOURCE_MAX = date(2011, 12, 1)
PERIOD_PATTERN = re.compile(r"([0-9]{4})-(0[1-9]|1[0-2])\Z")
YEAR_DIR_PATTERN = re.compile(r"year=([0-9]{4})\Z")
MONTH_DIR_PATTERN = re.compile(r"month=(0[1-9]|1[0-2])\Z")
SQL_DIR = PROJECT_ROOT / "sql" / "operations"


class SimulatedFailure(RuntimeError):
    """Explicit development-only failure after quality, before watermark."""


def parse_period(value: str) -> date:
    match = PERIOD_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError(f"Invalid period {value!r}; expected YYYY-MM.")
    try:
        return date(int(match.group(1)), int(match.group(2)), 1)
    except ValueError as exc:
        raise ValueError(f"Invalid period {value!r}; expected YYYY-MM.") from exc


def format_period(period: date | None) -> str | None:
    return None if period is None else period.strftime("%Y-%m")


def next_month(period: date) -> date:
    if period.month == 12:
        return date(period.year + 1, 1, 1)
    return date(period.year, period.month + 1, 1)


def source_periods(
    root: Path = TRANSACTIONS_PARQUET_DIR,
    *,
    enforce_approved_range: bool = True,
) -> list[date]:
    """Read partition directory names only; do not scan unrelated Parquet."""
    if not root.is_dir():
        raise FileNotFoundError(f"Accepted Parquet root is missing: {root}")
    periods: set[date] = set()
    for year_dir in root.iterdir():
        year_match = YEAR_DIR_PATTERN.fullmatch(year_dir.name)
        if not year_dir.is_dir() or year_match is None:
            continue
        for month_dir in year_dir.iterdir():
            month_match = MONTH_DIR_PATTERN.fullmatch(month_dir.name)
            if month_dir.is_dir() and month_match is not None:
                periods.add(date(int(year_match.group(1)), int(month_match.group(1)), 1))
    ordered = sorted(periods)
    if not ordered:
        raise ValueError(f"No year/month Parquet partitions found under {root}.")
    if enforce_approved_range and (
        ordered[0] != APPROVED_SOURCE_MIN or ordered[-1] != APPROVED_SOURCE_MAX
    ):
        raise ValueError(
            f"Source period range is {format_period(ordered[0])} to "
            f"{format_period(ordered[-1])}; approved range is "
            f"{format_period(APPROVED_SOURCE_MIN)} to "
            f"{format_period(APPROVED_SOURCE_MAX)}."
        )
    for previous, current in zip(ordered, ordered[1:]):
        if next_month(previous) != current:
            raise ValueError(
                f"Missing source partition between {format_period(previous)} "
                f"and {format_period(current)}."
            )
    return ordered


def partition_path(period: date, root: Path = TRANSACTIONS_PARQUET_DIR) -> Path:
    return root / f"year={period.year:04d}" / f"month={period.month:02d}"


def row_belongs_to_period(
    batch_month: date, invoice_datetime: datetime, period: date
) -> bool:
    return (
        batch_month == period
        and invoice_datetime.year == period.year
        and invoice_datetime.month == period.month
    )


def inspect_period(
    period: date, root: Path = TRANSACTIONS_PARQUET_DIR
) -> dict[str, Any]:
    """Read only the requested partition and validate its physical rows."""
    folder = partition_path(period, root)
    if not folder.is_dir():
        raise FileNotFoundError(f"Accepted partition is missing: {folder}")
    files = sorted(folder.glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No Parquet files found in {folder}.")

    row_count = 0
    minimum: datetime | None = None
    maximum: datetime | None = None
    source_ids: set[str] = set()
    for path in files:
        parquet_file = pq.ParquetFile(path)
        _assert_physical_schema(parquet_file.schema_arrow, path)
        for batch in parquet_file.iter_batches(
            batch_size=8_192,
            columns=["batch_month", "invoice_datetime", "source_row_id"],
        ):
            for row in batch.to_pylist():
                invoice_datetime = row["invoice_datetime"]
                batch_month = row["batch_month"]
                source_row_id = row["source_row_id"]
                if (
                    not isinstance(invoice_datetime, datetime)
                    or not isinstance(batch_month, date)
                    or not row_belongs_to_period(batch_month, invoice_datetime, period)
                ):
                    raise ValueError(f"Out-of-period invoice/batch_month in {path}.")
                if (
                    not isinstance(source_row_id, str)
                    or SOURCE_ROW_ID_PATTERN.fullmatch(source_row_id) is None
                    or source_row_id in source_ids
                ):
                    raise ValueError(f"Invalid or duplicate source_row_id in {path}.")
                source_ids.add(source_row_id)
                row_count += 1
                minimum = (
                    invoice_datetime
                    if minimum is None or invoice_datetime < minimum
                    else minimum
                )
                maximum = (
                    invoice_datetime
                    if maximum is None or invoice_datetime > maximum
                    else maximum
                )
    if row_count == 0:
        raise ValueError(f"Accepted partition has no rows: {folder}")
    return {
        "period": format_period(period),
        "partition_path": str(folder),
        "file_count": len(files),
        "rows": row_count,
        "min_invoice_datetime": minimum,
        "max_invoice_datetime": maximum,
        "files": files,
    }


def decide_period(
    requested: date,
    watermark: date | None,
    source_min: date,
    source_max: date,
    *,
    force: bool = False,
) -> str:
    """Pure sequencing decision; watermark never acts as a uniqueness key."""
    if requested < source_min or requested > source_max:
        raise ValueError(
            f"Period {format_period(requested)} is outside source range "
            f"{format_period(source_min)} to {format_period(source_max)}."
        )
    if force:
        if watermark is None or requested > watermark:
            raise ValueError(
                "--force is only for a period already at or behind the watermark; "
                "it cannot create a future gap."
            )
        return "force_rerun"
    if watermark is not None and requested <= watermark:
        return "already_processed"
    expected = source_min if watermark is None else next_month(watermark)
    if requested != expected:
        raise ValueError(f"Expected next period: {format_period(expected)}.")
    return "next_period"


def next_expected_period(
    watermark: date | None, source_min: date, source_max: date
) -> date | None:
    next_period = source_min if watermark is None else next_month(watermark)
    return next_period if next_period <= source_max else None


def ensure_non_decreasing_watermark(before: date | None, after: date | None) -> date:
    if after is None or (before is not None and after < before):
        raise RuntimeError("Watermark must never move backwards or become NULL.")
    return after


def execute_verified_steps(
    stage: Callable[[], Any],
    warehouse: Callable[[], Any],
    quality: Callable[[], Any],
    advance: Callable[[], Any],
    *,
    simulate_failure_before_watermark: bool = False,
) -> tuple[Any, Any, Any, Any]:
    """Only the final callback can advance progress, after every gate succeeds."""
    staging_result = stage()
    warehouse_result = warehouse()
    quality_result = quality()
    if simulate_failure_before_watermark:
        print(
            "[controlled-failure] Intentionally failing after quality checks "
            "and before the watermark update.",
            flush=True,
        )
        raise SimulatedFailure("Intentional failure before watermark update.")
    return staging_result, warehouse_result, quality_result, advance()


def _print_json(label: str, value: Any) -> None:
    print(f"[{label}] {json.dumps(value, default=str, sort_keys=True)}", flush=True)


def _script_result(
    conn: pyodbc.Connection, script_name: str, *params: Any
) -> dict[str, Any] | None:
    script = (SQL_DIR / script_name).read_text(encoding="utf-8")
    cursor = conn.cursor()
    cursor.execute(script, *params)
    result: dict[str, Any] | None = None
    while True:
        if cursor.description is not None:
            names = [column[0] for column in cursor.description]
            rows = cursor.fetchall()
            if rows:
                if len(rows) != 1:
                    raise RuntimeError(f"{script_name} returned more than one result row.")
                result = dict(zip(names, rows[0], strict=True))
        if not cursor.nextset():
            break
    return result


def _watermark_table_exists(conn: pyodbc.Connection) -> bool:
    row = conn.cursor().execute(
        "SELECT OBJECT_ID(N'ops.pipeline_watermark', N'U')"
    ).fetchone()
    return row is not None and row[0] is not None


def read_watermark(conn: pyodbc.Connection) -> date | None:
    if not _watermark_table_exists(conn):
        raise RuntimeError("Watermark table is not initialized. Run 'init' manually.")
    rows = conn.cursor().execute(
        "SELECT last_processed_period FROM ops.pipeline_watermark "
        "WHERE pipeline_name = ?",
        PIPELINE_NAME,
    ).fetchall()
    if len(rows) != 1:
        raise RuntimeError("Expected exactly one retail_monthly_replay watermark row.")
    value = rows[0][0]
    return value.date() if isinstance(value, datetime) else value


def advance_watermark(
    conn: pyodbc.Connection, period: date, before: date | None
) -> date:
    """Compare-and-set, so a concurrent run cannot silently skip progress."""
    cursor = conn.cursor()
    rows = cursor.execute(
        "UPDATE ops.pipeline_watermark "
        "SET last_processed_period = ?, updated_at_utc = SYSUTCDATETIME() "
        "OUTPUT inserted.last_processed_period "
        "WHERE pipeline_name = ? "
        "AND ((last_processed_period IS NULL AND CONVERT(DATE, ?) IS NULL) "
        "OR last_processed_period = CONVERT(DATE, ?))",
        period,
        PIPELINE_NAME,
        before,
        before,
    ).fetchall()
    if len(rows) != 1:
        conn.rollback()
        raise RuntimeError("Watermark changed concurrently; no progress was recorded.")
    conn.commit()
    after = read_watermark(conn)
    if after != period:
        raise RuntimeError("Watermark update did not persist the requested period.")
    return ensure_non_decreasing_watermark(before, after)


def validate_period_result(result: dict[str, Any], input_rows: int) -> None:
    if result["staging_rows"] != input_rows:
        raise AssertionError("Parquet/staging period row counts differ.")
    if result["fact_rows"] != input_rows:
        raise AssertionError("Staging/fact period row counts differ.")
    if result["staging_distinct_ids"] != input_rows:
        raise AssertionError("Staging has duplicate period source_row_id values.")
    if result["fact_distinct_ids"] != input_rows:
        raise AssertionError("Fact has duplicate period source_row_id values.")
    zero_fields = (
        "staging_ids_missing_fact",
        "fact_ids_missing_staging",
        "orphan_date_keys",
        "orphan_customer_keys",
        "orphan_product_keys",
        "orphan_country_keys",
        "staging_negative_price_rows",
        "fact_negative_price_rows",
        "staging_wrong_invoice_month_rows",
        "fact_wrong_invoice_month_rows",
        "copied_value_mismatches",
        "dimension_mapping_mismatches",
    )
    for name in zero_fields:
        if result[name] != 0:
            raise AssertionError(f"Period quality check {name} = {result[name]}, expected 0.")
    for staging_name, fact_name in (
        ("staging_line_amount_sum", "fact_line_amount_sum"),
        ("staging_core_commercial_lines", "fact_core_commercial_lines"),
        ("staging_core_cancellation_lines", "fact_core_cancellation_lines"),
    ):
        if result[staging_name] != result[fact_name]:
            raise AssertionError(f"Period quality check {staging_name}/{fact_name} differs.")


def _connect_verified() -> pyodbc.Connection:
    settings = SqlServerSettings.from_environment()
    conn = connect(settings)
    try:
        _print_json("connection", verify_connection(conn, settings))
    except Exception:
        conn.close()
        raise
    return conn


def _source_range() -> tuple[date, date]:
    periods = source_periods()
    return periods[0], periods[-1]


def command_status() -> None:
    source_min, source_max = _source_range()
    conn = _connect_verified()
    try:
        initialized = _watermark_table_exists(conn)
        watermark = read_watermark(conn) if initialized else None
        _print_json(
            "status",
            {
                "pipeline_name": PIPELINE_NAME,
                "initialized": initialized,
                "watermark": format_period(watermark),
                "next_expected_period": format_period(
                    next_expected_period(watermark, source_min, source_max)
                ),
                "source_min_period": format_period(source_min),
                "source_max_period": format_period(source_max),
            },
        )
    finally:
        conn.close()


def command_check_period(value: str) -> None:
    period = parse_period(value)
    source_min, source_max = _source_range()
    if period < source_min or period > source_max:
        raise ValueError(f"Period {value} is outside the approved source range.")
    summary = inspect_period(period)
    conn = _connect_verified()
    try:
        if _watermark_table_exists(conn):
            watermark = read_watermark(conn)
            try:
                relative_state = decide_period(
                    period, watermark, source_min, source_max
                )
            except ValueError as exc:
                relative_state = str(exc)
        else:
            watermark = None
            relative_state = "not_initialized"
        _print_json(
            "check-period",
            {
                key: value for key, value in summary.items() if key != "files"
            }
            | {
                "watermark": format_period(watermark),
                "relative_to_watermark": relative_state,
            },
        )
    finally:
        conn.close()


def command_init() -> None:
    conn = _connect_verified()
    try:
        for script_name in (
            "001_create_ops_schema.sql",
            "002_create_pipeline_watermark.sql",
        ):
            _script_result(conn, script_name)
            print(f"[init] Applied {script_name}", flush=True)
        conn.commit()
        _print_json("init", {"watermark": format_period(read_watermark(conn))})
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def command_run(
    value: str, *, force: bool, simulate_failure_before_watermark: bool
) -> None:
    period = parse_period(value)
    source_min, source_max = _source_range()
    if period < source_min or period > source_max:
        raise ValueError(f"Period {value} is outside the approved source range.")
    summary = inspect_period(period)
    conn = _connect_verified()
    try:
        watermark_before = read_watermark(conn)
        decision = decide_period(
            period, watermark_before, source_min, source_max, force=force
        )
        if decision == "already_processed":
            _print_json(
                "run",
                {
                    "period": value,
                    "result": "already processed; no data or watermark changed",
                    "watermark": format_period(watermark_before),
                },
            )
            return

        def stage() -> dict[str, int]:
            result = load_accepted_parquet(
                conn, summary, require_full_staging_count=False
            )
            conn.commit()
            _print_json("staging", result)
            return result

        def warehouse() -> dict[str, Any]:
            result = _script_result(
                conn, "004_upsert_warehouse_period.sql", period, source_max
            )
            conn.commit()
            if result is None:
                raise RuntimeError("Warehouse period upsert returned no result.")
            _print_json("warehouse", result)
            return result

        def quality() -> dict[str, Any]:
            result = _script_result(conn, "005_validate_period.sql", period)
            if result is None:
                raise RuntimeError("Period validation returned no result.")
            validate_period_result(result, int(summary["rows"]))
            _print_json("quality", result)
            print("[quality] all period checks passed", flush=True)
            return result

        def advance() -> date:
            if force:
                after = read_watermark(conn)
                return ensure_non_decreasing_watermark(watermark_before, after)
            return advance_watermark(conn, period, watermark_before)

        staging_result, warehouse_result, _, watermark_after = execute_verified_steps(
            stage,
            warehouse,
            quality,
            advance,
            simulate_failure_before_watermark=simulate_failure_before_watermark,
        )
        _print_json(
            "done",
            {
                "period": value,
                "input_rows": summary["rows"],
                "staging_processed": staging_result["processed"],
                "staging_inserted": staging_result["inserted"],
                "staging_skipped_existing": staging_result["skipped_existing"],
                "dimension_changes": {
                    name: warehouse_result[name]
                    for name in (
                        "date_inserted", "customer_inserted", "product_inserted",
                        "product_updated", "country_inserted"
                    )
                },
                "fact_inserted": warehouse_result["fact_inserted"],
                "fact_skipped_existing": warehouse_result["fact_skipped_existing"],
                "quality_checks_passed": True,
                "watermark_before": format_period(watermark_before),
                "watermark_after": format_period(watermark_after),
                "force": force,
            },
        )
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Historical monthly retail replay")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Read-only watermark/source status")
    check = commands.add_parser("check-period", help="Read-only period input check")
    check.add_argument("--period", required=True)
    commands.add_parser("init", help="Create ops schema/table without resetting data")
    run = commands.add_parser("run", help="Replay one accepted historical month")
    run.add_argument("--period", required=True)
    run.add_argument("--force", action="store_true", help="Rerun a processed period")
    run.add_argument(
        "--simulate-failure-before-watermark",
        action="store_true",
        help="Development test only: fail after quality and before watermark",
    )
    args = parser.parse_args()
    if args.command == "status":
        command_status()
    elif args.command == "check-period":
        command_check_period(args.period)
    elif args.command == "init":
        command_init()
    elif args.command == "run":
        command_run(
            args.period,
            force=args.force,
            simulate_failure_before_watermark=args.simulate_failure_before_watermark,
        )


if __name__ == "__main__":
    main()

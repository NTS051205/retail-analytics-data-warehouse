"""Load accepted Phase 3 Parquet occurrences into SQL Server staging.

Only explicit --init and --load actions change the database. Each insert batch
commits independently; reruns skip source_row_id values that already exist.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any, Mapping

import pyarrow as pa
import pyarrow.parquet as pq
import pyodbc

from .config import PHASE3_SUMMARY_PATH, PROJECT_ROOT, TRANSACTIONS_PARQUET_DIR
from .db import SqlServerSettings, connect, verify_connection
from .quality import ACCEPTED_CLASSIFICATIONS, QUALITY_FLAG_ORDER
from .transform import ACCEPTED_ARROW_SCHEMA, ACCEPTED_OUTPUT_COLUMNS


EXPECTED_ACCEPTED_ROWS = 1_067_366
EXPECTED_PARTITIONS = 25
DEFAULT_BATCH_SIZE = 5_000
LOOKUP_CHUNK_SIZE = 1_000  # comfortably below SQL Server's parameter limit
SQL_SCRIPT_DIR = PROJECT_ROOT / "sql" / "staging"
SOURCE_ROW_ID_PATTERN = re.compile(r"[0-9a-f]{64}\Z")

# Actual accepted-Parquet maxima were 14, 7, 12, 35, 5, 20, 24, 70.
# These bounded SQL widths retain the approved Phase 2 targets with headroom.
SQL_TEXT_LIMITS = {
    "source_sheet": 64,
    "invoice_no": 32,
    "stock_code": 64,
    "description": 512,
    "customer_id": 32,
    "country": 128,
    "classification": 32,
    "quality_flags": 256,
}

SQL_COLUMNS = tuple(ACCEPTED_OUTPUT_COLUMNS)
SQL_SAMPLE_COLUMNS = (
    "source_row_id",
    "invoice_no",
    "stock_code",
    "description",
    "quantity",
    "unit_price",
    "line_amount",
    "customer_id",
    "is_cancelled",
    "classification",
    "quality_flags",
)

# name, SQL type, max characters, precision, scale, nullable, required collation
EXPECTED_SQL_SCHEMA = (
    ("source_sheet", "nvarchar", 64, None, None, False, None),
    ("source_row_number", "bigint", None, None, None, False, None),
    ("source_row_id", "char", 64, None, None, False, "Latin1_General_100_BIN2"),
    ("invoice_no", "nvarchar", 32, None, None, False, None),
    ("stock_code", "nvarchar", 64, None, None, False, "Latin1_General_100_BIN2"),
    ("description", "nvarchar", 512, None, None, True, None),
    ("quantity", "int", None, None, None, False, None),
    ("invoice_datetime", "datetime2", None, None, 3, False, None),
    ("unit_price", "decimal", None, 19, 4, False, None),
    ("customer_id", "nvarchar", 32, None, None, True, None),
    ("country", "nvarchar", 128, None, None, True, None),
    ("line_amount", "decimal", None, 30, 4, False, None),
    ("is_cancelled", "bit", None, None, None, False, None),
    ("batch_month", "date", None, None, None, False, None),
    ("loaded_at", "datetime2", None, None, 6, False, None),
    ("classification", "nvarchar", 32, None, None, False, None),
    ("quality_flags", "nvarchar", 256, None, None, False, None),
    *((flag, "bit", None, None, None, False, None) for flag in QUALITY_FLAG_ORDER),
)


def _utf16_units(value: str) -> int:
    """SQL Server NVARCHAR lengths count UTF-16 code units."""
    return len(value.encode("utf-16-le")) // 2


def _text(value: Any, name: str, *, nullable: bool = False) -> str | None:
    if value is None:
        if nullable:
            return None
        raise ValueError(f"{name} is required in accepted Parquet.")
    if not isinstance(value, str):
        raise ValueError(f"{name} must remain text, found {type(value).__name__}.")
    if _utf16_units(value) > SQL_TEXT_LIMITS[name]:
        raise ValueError(
            f"{name} exceeds NVARCHAR({SQL_TEXT_LIMITS[name]}); adjust and review "
            "the SQL schema before loading."
        )
    return value


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer.")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} is outside the approved SQL integer range.")
    return value


def _bit(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean.")
    return value


def _decimal_text(value: Any, name: str, *, precision: int) -> str:
    """Pass exact decimal text to a parameterized SQL CONVERT; never use float."""
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal.")
    with localcontext() as context:
        context.prec = 40
        try:
            scaled = value.quantize(Decimal("0.0001"))
        except InvalidOperation as exc:
            raise ValueError(f"{name} does not fit scale 4.") from exc
    # copy_abs() does not round through the active Decimal context. abs() can
    # round a 30-digit boundary value before this comparison.
    if scaled != value or value.copy_abs() >= Decimal(10) ** (precision - 4):
        raise ValueError(f"{name} does not fit DECIMAL({precision},4) exactly.")
    return f"{value:.4f}"


def _invoice_datetime(value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is not None:
        raise ValueError("invoice_datetime must be a timezone-naive datetime.")
    if value.microsecond % 1_000 != 0:
        raise ValueError("invoice_datetime exceeds DATETIME2(3) precision.")
    return value


def _loaded_at_utc(value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("loaded_at must be a timezone-aware UTC datetime.")
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _batch_month(value: Any) -> date:
    if not isinstance(value, date) or isinstance(value, datetime) or value.day != 1:
        raise ValueError("batch_month must be the first date of its month.")
    return value


def prepare_sql_row(record: Mapping[str, Any]) -> tuple[Any, ...]:
    """Preserve one accepted occurrence in the exact stg.transactions column order."""
    source_row_id = record.get("source_row_id")
    if not isinstance(source_row_id, str) or not SOURCE_ROW_ID_PATTERN.fullmatch(source_row_id):
        raise ValueError("source_row_id must be the original lowercase SHA-256 hex value.")

    classification = _text(record.get("classification"), "classification")
    if classification not in ACCEPTED_CLASSIFICATIONS:
        raise ValueError("Only Phase 3 accepted classifications may enter staging.")

    unit_price = record.get("unit_price")
    if not isinstance(unit_price, Decimal) or unit_price < 0:
        raise ValueError("Accepted staging unit_price must be a nonnegative Decimal.")

    prepared = {
        "source_sheet": _text(record.get("source_sheet"), "source_sheet"),
        "source_row_number": _integer(
            record.get("source_row_number"), "source_row_number", 2, 2**63 - 1
        ),
        "source_row_id": source_row_id,
        "invoice_no": _text(record.get("invoice_no"), "invoice_no"),
        "stock_code": _text(record.get("stock_code"), "stock_code"),
        "description": _text(record.get("description"), "description", nullable=True),
        "quantity": _integer(record.get("quantity"), "quantity", -(2**31), 2**31 - 1),
        "invoice_datetime": _invoice_datetime(record.get("invoice_datetime")),
        "unit_price": _decimal_text(unit_price, "unit_price", precision=19),
        "customer_id": _text(record.get("customer_id"), "customer_id", nullable=True),
        "country": _text(record.get("country"), "country", nullable=True),
        "line_amount": _decimal_text(
            record.get("line_amount"), "line_amount", precision=30
        ),
        "is_cancelled": _bit(record.get("is_cancelled"), "is_cancelled"),
        "batch_month": _batch_month(record.get("batch_month")),
        "loaded_at": _loaded_at_utc(record.get("loaded_at")),
        "classification": classification,
        "quality_flags": _text(record.get("quality_flags"), "quality_flags"),
    }
    for flag in QUALITY_FLAG_ORDER:
        prepared[flag] = _bit(record.get(flag), flag)
    return tuple(prepared[column] for column in SQL_COLUMNS)


def _partition_path(path: Path, root: Path) -> str:
    parts = path.parent.relative_to(root).parts
    if (
        len(parts) != 2
        or re.fullmatch(r"year=[0-9]{4}", parts[0]) is None
        or re.fullmatch(r"month=(0[1-9]|1[0-2])", parts[1]) is None
    ):
        raise ValueError(f"Invalid Hive year/month partition path: {path}")
    return "/".join(parts)


def _parquet_files(root: Path) -> list[Path]:
    if not root.is_dir():
        raise FileNotFoundError(f"Accepted Phase 3 Parquet directory is missing: {root}")
    files = sorted(root.rglob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No accepted Parquet files were found under {root}")
    return files


def _assert_physical_schema(schema: pa.Schema, path: Path) -> None:
    expected = [(field.name, field.type) for field in ACCEPTED_ARROW_SCHEMA]
    actual = [(field.name, field.type) for field in schema]
    if actual != expected:
        raise ValueError(
            f"Parquet physical column/type schema mismatch in {path}. "
            f"Expected {expected}; found {actual}."
        )


def inspect_parquet_input(root: Path = TRANSACTIONS_PARQUET_DIR) -> dict[str, Any]:
    """Read-only full preflight before any SQL inserts or DDL."""
    files = _parquet_files(root)
    partitions: set[str] = set()
    maxima = {name: 0 for name in SQL_TEXT_LIMITS}
    row_count = 0

    for path in files:
        partitions.add(_partition_path(path, root))
        parquet_file = pq.ParquetFile(path)
        _assert_physical_schema(parquet_file.schema_arrow, path)
        row_count += parquet_file.metadata.num_rows

        scan_columns = [*SQL_TEXT_LIMITS, "source_row_id"]
        for batch in parquet_file.iter_batches(batch_size=8_192, columns=scan_columns):
            values = batch.to_pydict()
            for name in SQL_TEXT_LIMITS:
                for item in values[name]:
                    if item is None:
                        if name not in {"description", "customer_id", "country"}:
                            raise ValueError(f"Required {name} is null in {path}.")
                        continue
                    if not isinstance(item, str):
                        raise ValueError(f"{name} is not text in {path}.")
                    length = _utf16_units(item)
                    maxima[name] = max(maxima[name], length)
                    if length > SQL_TEXT_LIMITS[name]:
                        raise ValueError(
                            f"{name} has {length} UTF-16 units in {path}; "
                            f"NVARCHAR({SQL_TEXT_LIMITS[name]}) is too short."
                        )
            for source_row_id in values["source_row_id"]:
                if (
                    not isinstance(source_row_id, str)
                    or SOURCE_ROW_ID_PATTERN.fullmatch(source_row_id) is None
                ):
                    raise ValueError(f"Invalid source_row_id in {path}.")

    if row_count != EXPECTED_ACCEPTED_ROWS:
        raise ValueError(
            f"Accepted Parquet has {row_count} rows; expected {EXPECTED_ACCEPTED_ROWS}."
        )
    if len(partitions) != EXPECTED_PARTITIONS:
        raise ValueError(
            f"Accepted Parquet has {len(partitions)} partitions; "
            f"expected {EXPECTED_PARTITIONS}."
        )
    if not PHASE3_SUMMARY_PATH.is_file():
        raise FileNotFoundError(f"Phase 3 summary is missing: {PHASE3_SUMMARY_PATH}")
    phase3_summary = json.loads(PHASE3_SUMMARY_PATH.read_text(encoding="utf-8"))
    if phase3_summary.get("accepted_rows") != row_count:
        raise ValueError("Accepted Parquet count differs from the Phase 3 summary.")

    return {
        "rows": row_count,
        "file_count": len(files),
        "partition_count": len(partitions),
        "maximum_utf16_units": maxima,
        "files": files,
    }


def _assert_sql_table_ready(cursor: pyodbc.Cursor) -> None:
    rows = cursor.execute(
        "SELECT c.name, t.name, c.max_length, c.precision, c.scale, "
        "c.is_nullable, c.collation_name "
        "FROM sys.columns AS c "
        "JOIN sys.types AS t ON t.user_type_id = c.user_type_id "
        "WHERE c.object_id = OBJECT_ID(N'stg.transactions', N'U') "
        "ORDER BY c.column_id"
    ).fetchall()
    actual = tuple(
        (
            name,
            type_name,
            (max_length // 2 if type_name == "nvarchar" else max_length)
            if type_name in {"nvarchar", "char"} else None,
            precision if type_name == "decimal" else None,
            scale if type_name in {"decimal", "datetime2"} else None,
            bool(nullable),
            collation if name in {"source_row_id", "stock_code"} else None,
        )
        for name, type_name, max_length, precision, scale, nullable, collation in rows
    )
    if actual != EXPECTED_SQL_SCHEMA:
        raise RuntimeError(
            "stg.transactions is missing or its column types/order differ from "
            "002_create_transactions.sql. Run --init and inspect the reported mismatch."
        )

    unique_rows = cursor.execute(
        "SELECT i.index_id "
        "FROM sys.indexes AS i "
        "JOIN sys.index_columns AS ic "
        "ON ic.object_id = i.object_id AND ic.index_id = i.index_id "
        "JOIN sys.columns AS c "
        "ON c.object_id = ic.object_id AND c.column_id = ic.column_id "
        "WHERE i.object_id = OBJECT_ID(N'stg.transactions', N'U') "
        "AND i.is_unique = 1 AND i.is_disabled = 0 AND i.has_filter = 0 "
        "AND ic.key_ordinal > 0 "
        "GROUP BY i.index_id "
        "HAVING COUNT(*) = 1 AND MAX(c.name) = N'source_row_id'"
    ).fetchall()
    if not unique_rows:
        raise RuntimeError("stg.transactions lacks an enabled unique source_row_id key.")


def initialize_staging(conn: pyodbc.Connection) -> None:
    cursor = conn.cursor()
    try:
        for script_name in (
            "001_create_staging_schema.sql",
            "002_create_transactions.sql",
        ):
            script_path = SQL_SCRIPT_DIR / script_name
            script = script_path.read_text(encoding="utf-8")
            cursor.execute(script)
            while cursor.nextset():
                pass
            print(f"[init] Applied {script_name}", flush=True)
        _assert_sql_table_ready(cursor)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _existing_ids(cursor: pyodbc.Cursor, source_row_ids: list[str]) -> set[str]:
    existing: set[str] = set()
    for start in range(0, len(source_row_ids), LOOKUP_CHUNK_SIZE):
        chunk = source_row_ids[start : start + LOOKUP_CHUNK_SIZE]
        placeholders = ",".join("?" for _ in chunk)
        query = (
            "SELECT source_row_id FROM stg.transactions "
            f"WHERE source_row_id IN ({placeholders})"
        )
        existing.update(str(row[0]).strip() for row in cursor.execute(query, *chunk))
    return existing


_SQL_IDENTIFIERS = ", ".join(f"[{column}]" for column in SQL_COLUMNS)
_SQL_VALUES = ", ".join(
    "CONVERT(DECIMAL(19,4), ?)" if column == "unit_price"
    else "CONVERT(DECIMAL(30,4), ?)" if column == "line_amount"
    else "?"
    for column in SQL_COLUMNS
)
INSERT_SQL = f"INSERT INTO stg.transactions ({_SQL_IDENTIFIERS}) VALUES ({_SQL_VALUES})"


def load_accepted_parquet(
    conn: pyodbc.Connection,
    input_summary: Mapping[str, Any],
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict[str, int]:
    if not 1 <= batch_size <= 10_000:
        raise ValueError("batch_size must be between 1 and 10,000.")
    cursor = conn.cursor()
    _assert_sql_table_ready(cursor)
    processed = inserted = skipped_existing = 0

    for path in input_summary["files"]:
        file_processed = file_inserted = file_skipped = 0
        parquet_file = pq.ParquetFile(path)
        for batch in parquet_file.iter_batches(
            batch_size=batch_size, columns=list(SQL_COLUMNS)
        ):
            try:
                sql_rows = [prepare_sql_row(record) for record in batch.to_pylist()]
                source_row_ids = [str(row[2]) for row in sql_rows]
                if len(set(source_row_ids)) != len(source_row_ids):
                    raise ValueError(f"Duplicate source_row_id values within {path} batch.")

                existing = _existing_ids(cursor, source_row_ids)
                new_rows = [row for row in sql_rows if row[2] not in existing]
                if new_rows:
                    cursor.fast_executemany = True
                    cursor.executemany(INSERT_SQL, new_rows)
                    cursor.fast_executemany = False
                conn.commit()
            except Exception as exc:
                conn.rollback()
                raise RuntimeError(
                    f"Load failed in {path}; current batch was rolled back. "
                    "Earlier committed batches remain safe to resume."
                ) from exc

            count = len(sql_rows)
            inserted_count = len(new_rows)
            skipped_count = count - inserted_count
            processed += count
            inserted += inserted_count
            skipped_existing += skipped_count
            file_processed += count
            file_inserted += inserted_count
            file_skipped += skipped_count
        print(
            f"[load] {_partition_path(path, TRANSACTIONS_PARQUET_DIR)}: "
            f"processed={file_processed:,}, inserted={file_inserted:,}, "
            f"skipped_existing={file_skipped:,}",
            flush=True,
        )

    if processed != int(input_summary["rows"]):
        raise AssertionError(
            f"Processed {processed} rows, but input preflight found {input_summary['rows']}."
        )
    if processed != inserted + skipped_existing:
        raise AssertionError("Processed rows do not reconcile to inserted plus skipped.")
    staging_count = int(cursor.execute("SELECT COUNT_BIG(*) FROM stg.transactions").fetchone()[0])
    if staging_count != EXPECTED_ACCEPTED_ROWS:
        raise AssertionError(
            f"stg.transactions has {staging_count} rows; expected "
            f"{EXPECTED_ACCEPTED_ROWS}. Run the SQL validation script before proceeding."
        )
    return {
        "processed": processed,
        "inserted": inserted,
        "skipped_existing": skipped_existing,
        "staging_rows": staging_count,
    }


def _sample_categories(record: Mapping[str, Any]) -> list[str]:
    categories: list[str] = []
    if (
        record["classification"] == "ACCEPT"
        and not record["is_cancelled"]
        and record["quantity"] > 0
        and record["unit_price"] > 0
    ):
        categories.append("normal_clean")
    if record["MISSING_CUSTOMER"]:
        categories.append("missing_customer")
    if record["ZERO_PRICE"]:
        categories.append("zero_price")
    if (
        not record["is_cancelled"]
        and record["quantity"] < 0
        and record["QUANTITY_SIGN_MISMATCH"]
    ):
        categories.append("non_c_negative_quantity")
    if record["is_cancelled"] and record["quantity"] < 0:
        categories.append("cancellation")
    if record["MISSING_DESCRIPTION"]:
        categories.append("missing_description")
    return categories


def compare_representative_rows(conn: pyodbc.Connection, files: list[Path]) -> int:
    """Read-only comparison of six actual Parquet rows with their SQL copies."""
    selected: dict[str, dict[str, Any]] = {}
    needed = {
        "normal_clean",
        "missing_customer",
        "zero_price",
        "non_c_negative_quantity",
        "cancellation",
        "missing_description",
    }
    scan_columns = list(
        dict.fromkeys(
            [
                *SQL_SAMPLE_COLUMNS,
                "MISSING_CUSTOMER",
                "ZERO_PRICE",
                "QUANTITY_SIGN_MISMATCH",
                "MISSING_DESCRIPTION",
            ]
        )
    )
    for path in files:
        for batch in pq.ParquetFile(path).iter_batches(
            batch_size=8_192, columns=scan_columns
        ):
            for record in batch.to_pylist():
                for category in _sample_categories(record):
                    previous = selected.get(category)
                    if previous is None or record["source_row_id"] < previous["source_row_id"]:
                        selected[category] = record
    missing = needed - selected.keys()
    if missing:
        raise AssertionError(f"No real Parquet sample found for: {sorted(missing)}")

    cursor = conn.cursor()
    columns_sql = ", ".join(f"[{column}]" for column in SQL_SAMPLE_COLUMNS)
    for category in sorted(needed):
        source = selected[category]
        row = cursor.execute(
            f"SELECT {columns_sql} FROM stg.transactions WHERE source_row_id = ?",
            source["source_row_id"],
        ).fetchone()
        if row is None:
            raise AssertionError(
                f"SQL is missing {category} source_row_id {source['source_row_id']}."
            )
        sql_record = dict(zip(SQL_SAMPLE_COLUMNS, row, strict=True))
        for column in SQL_SAMPLE_COLUMNS:
            if source[column] != sql_record[column]:
                raise AssertionError(
                    f"{category} source_row_id {source['source_row_id']} differs "
                    f"between Parquet and SQL at {column}: "
                    f"Parquet={source[column]!r}, SQL={sql_record[column]!r}."
                )
        print(f"[sample] {category}: {source['source_row_id']} matches", flush=True)
    return len(needed)


def _print_input_summary(summary: Mapping[str, Any]) -> None:
    display = {key: value for key, value in summary.items() if key != "files"}
    print(json.dumps(display, indent=2), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 4 SQL Server staging loader")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check-input", action="store_true", help="Inspect Parquet only")
    action.add_argument("--check-connection", action="store_true", help="Connect read-only")
    action.add_argument("--init", action="store_true", help="Create/check the stg schema and table")
    action.add_argument("--load", action="store_true", help="Load accepted Parquet into staging")
    action.add_argument("--compare-samples", action="store_true", help="Compare real Parquet/SQL rows")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    args = parser.parse_args()

    if args.check_input:
        _print_input_summary(inspect_parquet_input())
        return

    input_summary = None
    if args.load or args.compare_samples:
        input_summary = inspect_parquet_input()
        _print_input_summary(input_summary)

    settings = SqlServerSettings.from_environment()
    conn = connect(settings)
    try:
        identity = verify_connection(conn, settings)
        print(f"[connection] {json.dumps(identity)}", flush=True)
        if args.check_connection:
            print("[connection] successful", flush=True)
        elif args.init:
            initialize_staging(conn)
            print("[init] stg.transactions is ready", flush=True)
        elif args.load:
            assert input_summary is not None
            result = load_accepted_parquet(
                conn, input_summary, batch_size=args.batch_size
            )
            print(f"[done] {json.dumps(result)}", flush=True)
        elif args.compare_samples:
            assert input_summary is not None
            _assert_sql_table_ready(conn.cursor())
            count = compare_representative_rows(conn, input_summary["files"])
            print(f"[done] {count} representative Parquet/SQL rows match", flush=True)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

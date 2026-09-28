"""Phase 6 pure sequencing and local Parquet tests; no SQL Server calls."""

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.pipeline import (
    SimulatedFailure,
    decide_period,
    ensure_non_decreasing_watermark,
    execute_verified_steps,
    inspect_period,
    next_expected_period,
    next_month,
    parse_period,
    partition_path,
    row_belongs_to_period,
    source_periods,
    validate_period_result,
)
from src.transform import ACCEPTED_ARROW_SCHEMA


@pytest.mark.parametrize(
    "text, expected",
    [
        ("2009-12", date(2009, 12, 1)),
        ("2010-01", date(2010, 1, 1)),
        ("2011-12", date(2011, 12, 1)),
    ],
)
def test_parse_valid_periods(text, expected) -> None:
    assert parse_period(text) == expected


@pytest.mark.parametrize("text", ["2010-13", "10-2010", "2010/05", "abc", "0000-01"])
def test_parse_rejects_invalid_periods(text) -> None:
    with pytest.raises(ValueError, match="Invalid period"):
        parse_period(text)


def test_first_period_next_month_and_year_rollover() -> None:
    start = date(2009, 12, 1)
    last = date(2011, 12, 1)
    assert next_expected_period(None, start, last) == start
    assert next_month(start) == date(2010, 1, 1)
    assert next_month(date(2010, 12, 1)) == date(2011, 1, 1)
    assert next_expected_period(last, start, last) is None


def test_chronological_decisions_and_already_processed() -> None:
    start = date(2009, 12, 1)
    last = date(2011, 12, 1)
    assert decide_period(start, None, start, last) == "next_period"
    assert decide_period(date(2010, 1, 1), start, start, last) == "next_period"
    assert decide_period(start, start, start, last) == "already_processed"
    assert (
        decide_period(date(2010, 1, 1), date(2010, 2, 1), start, last)
        == "already_processed"
    )


def test_out_of_range_and_future_gap_rejected() -> None:
    start = date(2009, 12, 1)
    last = date(2011, 12, 1)
    with pytest.raises(ValueError, match="outside source range"):
        decide_period(date(2009, 11, 1), None, start, last)
    with pytest.raises(ValueError, match="outside source range"):
        decide_period(date(2012, 1, 1), last, start, last)
    with pytest.raises(ValueError, match="Expected next period: 2010-02"):
        decide_period(date(2010, 3, 1), date(2010, 1, 1), start, last)


def test_force_only_reruns_at_or_behind_watermark() -> None:
    start = date(2009, 12, 1)
    last = date(2011, 12, 1)
    watermark = date(2010, 2, 1)
    assert decide_period(start, watermark, start, last, force=True) == "force_rerun"
    assert decide_period(watermark, watermark, start, last, force=True) == "force_rerun"
    with pytest.raises(ValueError, match="only for a period already"):
        decide_period(date(2010, 3, 1), watermark, start, last, force=True)
    with pytest.raises(ValueError, match="only for a period already"):
        decide_period(start, None, start, last, force=True)


def test_watermark_cannot_move_backwards_or_become_null() -> None:
    before = date(2010, 2, 1)
    assert ensure_non_decreasing_watermark(before, before) == before
    assert ensure_non_decreasing_watermark(before, date(2010, 3, 1)) == date(2010, 3, 1)
    with pytest.raises(RuntimeError, match="never move backwards"):
        ensure_non_decreasing_watermark(before, date(2010, 1, 1))
    with pytest.raises(RuntimeError, match="never move backwards"):
        ensure_non_decreasing_watermark(before, None)


def test_watermark_callback_occurs_only_after_all_success() -> None:
    events: list[str] = []

    def step(name: str) -> str:
        events.append(name)
        return name

    results = execute_verified_steps(
        lambda: step("staging"),
        lambda: step("warehouse"),
        lambda: step("quality"),
        lambda: step("watermark"),
    )
    assert results == ("staging", "warehouse", "quality", "watermark")
    assert events == ["staging", "warehouse", "quality", "watermark"]


def test_failed_quality_or_controlled_failure_does_not_advance() -> None:
    events: list[str] = []

    def fail_quality() -> None:
        events.append("quality")
        raise AssertionError("quality mismatch")

    with pytest.raises(AssertionError, match="quality mismatch"):
        execute_verified_steps(
            lambda: events.append("staging"),
            lambda: events.append("warehouse"),
            fail_quality,
            lambda: events.append("watermark"),
        )
    assert events == ["staging", "warehouse", "quality"]

    events.clear()
    with pytest.raises(SimulatedFailure, match="Intentional failure"):
        execute_verified_steps(
            lambda: events.append("staging"),
            lambda: events.append("warehouse"),
            lambda: events.append("quality"),
            lambda: events.append("watermark"),
            simulate_failure_before_watermark=True,
        )
    assert events == ["staging", "warehouse", "quality"]


def _accepted_row(period: date, *, source_row_id: str) -> dict:
    return {
        "source_sheet": "Year 2010-2011",
        "source_row_number": 2,
        "source_row_id": source_row_id,
        "invoice_no": "500001",
        "stock_code": "ABC",
        "description": "Example",
        "quantity": 1,
        "invoice_datetime": datetime(period.year, period.month, 5, 12),
        "unit_price": Decimal("2.5000"),
        "customer_id": "12345",
        "country": "United Kingdom",
        "line_amount": Decimal("2.5000"),
        "is_cancelled": False,
        "batch_month": period,
        "loaded_at": datetime(2026, 9, 28, tzinfo=timezone.utc),
        "classification": "ACCEPT",
        "quality_flags": "",
        "MISSING_CUSTOMER": False,
        "INVALID_CUSTOMER_ID": False,
        "MISSING_DESCRIPTION": False,
        "ZERO_PRICE": False,
        "QUANTITY_SIGN_MISMATCH": False,
        "NONSTANDARD_INVOICE": False,
        "MISSING_COUNTRY": False,
    }


def _write_period(root, period: date, row: dict) -> None:
    folder = partition_path(period, root)
    folder.mkdir(parents=True)
    table = pa.Table.from_pylist([row], schema=ACCEPTED_ARROW_SCHEMA)
    pq.write_table(table, folder / "part-0.parquet")


def test_partition_resolution_and_single_period_read(tmp_path) -> None:
    may = date(2010, 5, 1)
    june = date(2010, 6, 1)
    _write_period(tmp_path, may, _accepted_row(may, source_row_id="a" * 64))
    _write_period(tmp_path, june, _accepted_row(june, source_row_id="b" * 64))

    assert source_periods(tmp_path, enforce_approved_range=False) == [may, june]
    summary = inspect_period(may, tmp_path)
    assert summary["rows"] == 1
    assert summary["file_count"] == 1
    assert Path(summary["partition_path"]).parts[-2:] == ("year=2010", "month=05")
    assert all("month=05" in str(path) for path in summary["files"])
    assert summary["min_invoice_datetime"] == datetime(2010, 5, 5, 12)


def test_out_of_period_row_is_rejected(tmp_path) -> None:
    may = date(2010, 5, 1)
    wrong = _accepted_row(date(2010, 6, 1), source_row_id="c" * 64)
    _write_period(tmp_path, may, wrong)
    assert not row_belongs_to_period(wrong["batch_month"], wrong["invoice_datetime"], may)
    with pytest.raises(ValueError, match="Out-of-period"):
        inspect_period(may, tmp_path)


def test_period_quality_requires_identical_counts_and_sums() -> None:
    result = {
        "staging_rows": 1,
        "fact_rows": 1,
        "staging_distinct_ids": 1,
        "fact_distinct_ids": 1,
        "staging_ids_missing_fact": 0,
        "fact_ids_missing_staging": 0,
        "orphan_date_keys": 0,
        "orphan_customer_keys": 0,
        "orphan_product_keys": 0,
        "orphan_country_keys": 0,
        "staging_negative_price_rows": 0,
        "fact_negative_price_rows": 0,
        "staging_wrong_invoice_month_rows": 0,
        "fact_wrong_invoice_month_rows": 0,
        "copied_value_mismatches": 0,
        "dimension_mapping_mismatches": 0,
        "staging_line_amount_sum": Decimal("2.5000"),
        "fact_line_amount_sum": Decimal("2.5000"),
        "staging_core_commercial_lines": 1,
        "fact_core_commercial_lines": 1,
        "staging_core_cancellation_lines": 0,
        "fact_core_cancellation_lines": 0,
    }
    validate_period_result(result, 1)
    result["fact_line_amount_sum"] = Decimal("2.4900")
    with pytest.raises(AssertionError, match="line_amount_sum"):
        validate_period_result(result, 1)

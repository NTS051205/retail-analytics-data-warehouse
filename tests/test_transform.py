"""Business-behavior tests for the Phase 3 transformation."""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal, localcontext

import pandas as pd
import pyarrow.dataset as ds
import pytest

from src.extract import attach_source_lineage, make_source_row_id
from src.profiling.common import EXPECTED_COLUMNS
from src.quality import ACCEPT, ACCEPT_WITH_QUALITY_FLAG, REJECT
from src.transform import (
    ACCEPTED_OUTPUT_COLUMNS,
    transform_source,
    write_accepted_parquet,
)


FIXED_LOADED_AT = pd.Timestamp("2026-09-21T03:04:05Z")
VALID_ROW = {
    "Invoice": 489434.0,
    "StockCode": "85048",
    "Description": "  RED WOOLLY HOTTIE  ",
    "Quantity": 2,
    "InvoiceDate": pd.Timestamp("2009-12-01 07:45:00"),
    "Price": 6.95,
    "Customer ID": 13085.0,
    "Country": " United Kingdom ",
}


def source_frame(
    *overrides: dict[str, object],
    sheet_name: str = "Year 2009-2010",
) -> pd.DataFrame:
    rows = [{**VALID_ROW, **row} for row in (overrides or ({},))]
    return attach_source_lineage(pd.DataFrame(rows), sheet_name)


def transform_rows(*overrides: dict[str, object]):
    return transform_source(
        source_frame(*overrides),
        loaded_at=FIXED_LOADED_AT,
    )


def test_physical_excel_row_number_starts_at_two_for_each_sheet() -> None:
    raw = pd.DataFrame([VALID_ROW, VALID_ROW], index=[10, 20])
    first = attach_source_lineage(raw, "Year 2009-2010")
    second = attach_source_lineage(raw.iloc[:1], "Year 2010-2011")

    assert first["source_row_number"].tolist() == [2, 3]
    assert second["source_row_number"].tolist() == [2]


def test_source_row_id_matches_the_frozen_contract() -> None:
    expected = hashlib.sha256(b"Year 2009-2010|2").hexdigest()
    actual = make_source_row_id("Year 2009-2010", 2)

    assert actual == expected
    assert len(actual) == 64
    assert actual == actual.lower()
    assert make_source_row_id("Year 2009-2010", 2) == actual
    assert make_source_row_id("Year 2009-2010", 3) != actual
    assert make_source_row_id("Year 2010-2011", 2) != actual


def test_duplicate_looking_business_rows_survive_with_distinct_ids() -> None:
    result = transform_rows({}, {})

    assert result.source_count == 2
    assert result.accepted_count == 2
    assert result.rejected_count == 0
    assert result.accepted["source_row_id"].nunique() == 2
    assert result.accepted["invoice_no"].tolist() == ["489434", "489434"]


def test_duplicate_physical_identity_fails_the_batch() -> None:
    duplicated = pd.DataFrame([{**VALID_ROW}, {**VALID_ROW}])
    duplicated.insert(0, "source_row_number", [2, 2])
    duplicated.insert(0, "source_sheet", ["Sheet", "Sheet"])

    with pytest.raises(AssertionError, match="Duplicate physical source identity"):
        transform_source(duplicated, loaded_at=FIXED_LOADED_AT)


def test_valid_row_normalization_and_derived_fields() -> None:
    result = transform_rows()
    row = result.accepted.iloc[0]

    assert list(result.accepted.columns) == ACCEPTED_OUTPUT_COLUMNS
    assert row["invoice_no"] == "489434"
    assert row["stock_code"] == "85048"
    assert row["description"] == "RED WOOLLY HOTTIE"
    assert row["quantity"] == 2
    assert row["invoice_datetime"] == pd.Timestamp("2009-12-01 07:45:00")
    assert row["unit_price"] == Decimal("6.9500")
    assert row["customer_id"] == "13085"
    assert row["country"] == "United Kingdom"
    assert row["line_amount"] == Decimal("13.9000")
    assert not bool(row["is_cancelled"])
    assert row["batch_month"] == date(2009, 12, 1)
    assert row["loaded_at"] == FIXED_LOADED_AT
    assert row["classification"] == ACCEPT
    assert row["quality_flags"] == ""


def test_loaded_at_is_utc_and_losslessly_matches_microsecond_schema() -> None:
    supplied = pd.Timestamp("2026-09-21T03:04:05.123456789Z")
    result = transform_source(source_frame({}), loaded_at=supplied)
    actual = result.accepted.iloc[0]["loaded_at"]

    assert actual == pd.Timestamp("2026-09-21T03:04:05.123456Z")
    assert str(actual.tz) == "UTC"


def test_invoice_and_stock_code_normalization_preserve_contract_boundaries() -> None:
    result = transform_rows(
        {"Invoice": " c489435 ", "StockCode": "  ab01  "},
        {"Invoice": " a563185 ", "StockCode": "00123"},
        {"Invoice": 12.0, "StockCode": 123.0},
        {"Invoice": "123", "StockCode": "POST"},
    )
    accepted = result.accepted.reset_index(drop=True)

    assert accepted["invoice_no"].tolist() == ["C489435", "A563185", "12", "123"]
    assert accepted["stock_code"].tolist() == ["ab01", "00123", "123", "POST"]
    assert not bool(accepted.loc[0, "NONSTANDARD_INVOICE"])
    assert bool(accepted.loc[1, "NONSTANDARD_INVOICE"])
    assert accepted.loc[1, "classification"] == ACCEPT_WITH_QUALITY_FLAG


@pytest.mark.parametrize(
    ("invoice", "quantity", "cancelled", "mismatch"),
    [
        ("C123", -1, True, False),
        ("C123", 0, True, True),
        ("C123", 1, True, True),
        ("123", -1, False, True),
        ("123", 0, False, True),
        ("123", 1, False, False),
    ],
)
def test_quantity_sign_matrix(
    invoice: str,
    quantity: int,
    cancelled: bool,
    mismatch: bool,
) -> None:
    result = transform_rows({"Invoice": invoice, "Quantity": quantity})
    row = result.accepted.iloc[0]

    assert bool(row["is_cancelled"]) is cancelled
    assert bool(row["QUANTITY_SIGN_MISMATCH"]) is mismatch
    assert row["quantity"] == quantity


@pytest.mark.parametrize(
    ("quantity", "expected_reason"),
    [
        (None, "MISSING_QUANTITY"),
        ("", "MISSING_QUANTITY"),
        ("not-a-number", "INVALID_QUANTITY"),
        (1.5, "INVALID_QUANTITY"),
        (2_147_483_648, "INVALID_QUANTITY"),
        (float("inf"), "INVALID_QUANTITY"),
    ],
)
def test_invalid_quantity_reasons(quantity: object, expected_reason: str) -> None:
    result = transform_rows({"Quantity": quantity})
    assert result.rejected_count == 1
    assert result.rejected.iloc[0]["reject_reason"] == expected_reason


def test_sql_int_boundaries_are_retained() -> None:
    result = transform_rows(
        {"Invoice": "C1", "Quantity": -2_147_483_648},
        {"Invoice": "2", "Quantity": 2_147_483_647},
    )
    assert result.accepted["quantity"].tolist() == [-2_147_483_648, 2_147_483_647]


def test_line_amount_remains_exact_at_decimal_and_integer_boundaries() -> None:
    result = transform_rows(
        {"Quantity": 2_147_483_647, "Price": "999999999999999.9999"}
    )
    with localcontext() as context:
        context.prec = 40
        expected = Decimal("2147483647") * Decimal("999999999999999.9999")

    assert result.accepted.iloc[0]["line_amount"] == expected


def test_price_rules_and_signed_line_amount() -> None:
    result = transform_rows(
        {"Invoice": "C1", "Quantity": -2, "Price": "3.5000"},
        {"Invoice": "2", "Price": 0},
        {
            "Invoice": "A563186",
            "StockCode": "B",
            "Description": "Adjust bad debt",
            "Price": -11062.06,
            "Customer ID": None,
        },
    )
    accepted = result.accepted.reset_index(drop=True)
    rejected = result.rejected.iloc[0]

    assert accepted.loc[0, "line_amount"] == Decimal("-7.0000")
    assert accepted.loc[0, "classification"] == ACCEPT
    assert accepted.loc[1, "unit_price"] == Decimal("0.0000")
    assert accepted.loc[1, "line_amount"] == Decimal("0.0000")
    assert bool(accepted.loc[1, "ZERO_PRICE"])
    assert rejected["classification"] == REJECT
    assert "NEGATIVE_PRICE_ADJUSTMENT" in rejected["reject_reason"]


@pytest.mark.parametrize(
    ("price", "reason"),
    [
        (None, "MISSING_PRICE"),
        ("", "MISSING_PRICE"),
        ("not-a-number", "INVALID_PRICE"),
        ("1.23451", "INVALID_PRICE"),
        ("0.00001", "INVALID_PRICE"),
        ("1000000000000000.0000", "INVALID_PRICE"),
        (float("inf"), "INVALID_PRICE"),
    ],
)
def test_missing_or_invalid_price_reasons(price: object, reason: str) -> None:
    result = transform_rows({"Price": price})
    assert result.rejected.iloc[0]["reject_reason"] == reason


def test_customer_id_canonicalization_and_missing_behavior() -> None:
    result = transform_rows(
        {"Customer ID": 12345.0},
        {"Customer ID": "00123"},
        {"Customer ID": "1.2345E4"},
        {"Customer ID": None},
        {"Customer ID": "12345.5"},
        {"Customer ID": "ABC"},
    )
    accepted = result.accepted.reset_index(drop=True)

    assert accepted["customer_id"].iloc[:3].tolist() == ["12345", "123", "12345"]
    assert pd.isna(accepted.loc[3, "customer_id"])
    assert bool(accepted.loc[3, "MISSING_CUSTOMER"])
    assert pd.isna(accepted.loc[4, "customer_id"])
    assert bool(accepted.loc[4, "INVALID_CUSTOMER_ID"])
    assert pd.isna(accepted.loc[5, "customer_id"])
    assert bool(accepted.loc[5, "INVALID_CUSTOMER_ID"])


def test_description_and_country_are_trimmed_without_canonical_replacement() -> None:
    result = transform_rows(
        {"Description": " First Name ", "Country": " EIRE "},
        {"Description": "Second Name", "Country": "Unspecified"},
        {"Description": "   ", "Country": "   "},
    )
    accepted = result.accepted.reset_index(drop=True)

    assert accepted["description"].tolist()[:2] == ["First Name", "Second Name"]
    assert accepted["country"].tolist()[:2] == ["EIRE", "Unspecified"]
    assert pd.isna(accepted.loc[2, "description"])
    assert pd.isna(accepted.loc[2, "country"])
    assert bool(accepted.loc[2, "MISSING_DESCRIPTION"])
    assert bool(accepted.loc[2, "MISSING_COUNTRY"])


def test_multiple_reject_reasons_are_ordered_and_raw_fields_are_preserved() -> None:
    result = transform_rows(
        {
            "Invoice": None,
            "StockCode": " ",
            "InvoiceDate": "bad-date",
            "Quantity": None,
            "Price": None,
        }
    )
    row = result.rejected.iloc[0]

    assert row["reject_reason"] == (
        "MISSING_INVOICE;MISSING_STOCK_CODE;INVALID_INVOICE_DATE;"
        "MISSING_QUANTITY;MISSING_PRICE"
    )
    assert all(column in result.rejected.columns for column in EXPECTED_COLUMNS)
    assert row["classification"] == REJECT
    assert result.accepted_count == 0
    assert result.rejected_count == 1


def test_reconciliation_on_mixed_rows() -> None:
    result = transform_rows(
        {},
        {"Price": 0},
        {"Quantity": -1, "Invoice": "123"},
        {"Price": -1, "Invoice": "A1"},
    )

    assert result.source_count == result.accepted_count + result.rejected_count
    assert result.accepted_count == 3
    assert result.accepted_with_flags_count == 2
    assert result.rejected_count == 1
    assert result.accepted["source_row_id"].is_unique


def test_partitioned_parquet_has_stable_schema_and_zero_padded_paths(tmp_path) -> None:
    result = transform_rows(
        {"InvoiceDate": pd.Timestamp("2009-12-01 07:45:00")},
        {"Invoice": "489435", "InvoiceDate": pd.Timestamp("2010-01-02 08:00:00")},
    )
    output_dir = tmp_path / "transactions"
    summary = write_accepted_parquet(result.accepted, output_dir)

    assert (output_dir / "year=2009" / "month=12").is_dir()
    assert (output_dir / "year=2010" / "month=01").is_dir()
    assert summary["row_count"] == 2
    assert summary["partition_count"] == 2

    dataset = ds.dataset(str(output_dir), format="parquet", partitioning="hive")
    assert dataset.count_rows() == 2
    assert str(dataset.schema.field("unit_price").type) == "decimal128(19, 4)"
    assert str(dataset.schema.field("line_amount").type) == "decimal128(30, 4)"

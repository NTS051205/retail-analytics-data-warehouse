"""Pure conversion checks for Phase 4; no SQL Server is contacted."""

import re
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from src.load_staging import SQL_COLUMNS, prepare_sql_row


def accepted_record(**changes):
    record = {
        "source_sheet": "Year 2009-2010",
        "source_row_number": 2,
        "source_row_id": "a" * 64,
        "invoice_no": "489434",
        "stock_code": "ab01",
        "description": "Product",
        "quantity": 2,
        "invoice_datetime": datetime(2009, 12, 1, 7, 45),
        "unit_price": Decimal("6.9500"),
        "customer_id": "00123",
        "country": "United Kingdom",
        "line_amount": Decimal("13.9000"),
        "is_cancelled": False,
        "batch_month": date(2009, 12, 1),
        "loaded_at": datetime(2026, 9, 23, 1, 2, 3, 123456, tzinfo=timezone.utc),
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
    record.update(changes)
    return record


def prepared(record):
    return dict(zip(SQL_COLUMNS, prepare_sql_row(record), strict=True))


def test_exact_decimals_identifiers_and_utc_timestamp_survive_conversion() -> None:
    row = prepared(accepted_record())

    assert row["source_row_id"] == "a" * 64
    assert row["stock_code"] == "ab01"
    assert row["customer_id"] == "00123"
    assert row["unit_price"] == "6.9500"
    assert row["line_amount"] == "13.9000"
    assert row["invoice_datetime"] == datetime(2009, 12, 1, 7, 45)
    assert row["loaded_at"] == datetime(2026, 9, 23, 1, 2, 3, 123456)
    assert row["is_cancelled"] is False


def test_nullable_fields_become_sql_nulls_without_text_placeholders() -> None:
    row = prepared(
        accepted_record(
            description=None,
            customer_id=None,
            country=None,
            classification="ACCEPT WITH QUALITY FLAG",
            quality_flags="MISSING_CUSTOMER;MISSING_DESCRIPTION;MISSING_COUNTRY",
            MISSING_CUSTOMER=True,
            MISSING_DESCRIPTION=True,
            MISSING_COUNTRY=True,
        )
    )

    assert row["description"] is None
    assert row["customer_id"] is None
    assert row["country"] is None
    assert row["MISSING_CUSTOMER"] is True


def test_signed_line_amount_and_sql_decimal_boundaries_remain_exact() -> None:
    row = prepared(
        accepted_record(
            quantity=-2_147_483_648,
            line_amount=Decimal("-99999999999999999999999999.9999"),
            unit_price=Decimal("999999999999999.9999"),
        )
    )

    assert row["unit_price"] == "999999999999999.9999"
    assert row["line_amount"] == "-99999999999999999999999999.9999"


@pytest.mark.parametrize(
    "change, expected_message",
    [
        ({"classification": "REJECT"}, "accepted classifications"),
        ({"source_row_id": "A" * 64}, "lowercase SHA-256"),
        ({"unit_price": -1.0}, "nonnegative Decimal"),
        ({"unit_price": Decimal("1.00001")}, "DECIMAL(19,4)"),
        ({"source_row_number": 1}, "integer range"),
        ({"quantity": True}, "integer"),
        ({"ZERO_PRICE": 1}, "boolean"),
        ({"invoice_no": "X" * 33}, "NVARCHAR(32)"),
        ({"invoice_datetime": datetime(2009, 12, 1, 7, 45, 0, 1)}, "DATETIME2(3)"),
        ({"batch_month": date(2009, 12, 2)}, "first date"),
        ({"loaded_at": datetime(2026, 9, 23, 1, 2, 3)}, "timezone-aware"),
    ],
)
def test_unsafe_values_fail_before_sql_insert(change, expected_message) -> None:
    with pytest.raises(ValueError, match=re.escape(expected_message)):
        prepare_sql_row(accepted_record(**change))

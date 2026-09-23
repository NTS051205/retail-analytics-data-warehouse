"""Focused tests for the frozen Phase 2 quality and KPI predicates."""

from decimal import Decimal

import pandas as pd

from src.quality import (
    ACCEPT,
    ACCEPT_WITH_QUALITY_FLAG,
    QUALITY_FLAG_ORDER,
    REJECT,
    REJECT_REASON_ORDER,
    classify_rows,
    core_cancellation_line_mask,
    core_commercial_sale_mask,
    encode_ordered_codes,
)


def test_code_order_and_classification_precedence() -> None:
    index = pd.RangeIndex(3)
    quality_masks = {
        code: pd.Series(False, index=index) for code in QUALITY_FLAG_ORDER
    }
    quality_masks["MISSING_CUSTOMER"].iloc[[1, 2]] = True
    quality_masks["ZERO_PRICE"].iloc[2] = True
    quality_masks["NONSTANDARD_INVOICE"].iloc[2] = True
    flags = encode_ordered_codes(quality_masks, QUALITY_FLAG_ORDER, index=index)

    reject_masks = {
        code: pd.Series(False, index=index) for code in REJECT_REASON_ORDER
    }
    reject_masks["MISSING_STOCK_CODE"].iloc[2] = True
    reject_masks["MISSING_PRICE"].iloc[2] = True
    reasons = encode_ordered_codes(reject_masks, REJECT_REASON_ORDER, index=index)
    classification = classify_rows(reasons, flags)

    assert flags.tolist() == [
        "",
        "MISSING_CUSTOMER",
        "MISSING_CUSTOMER;ZERO_PRICE;NONSTANDARD_INVOICE",
    ]
    assert reasons.iloc[2] == "MISSING_STOCK_CODE;MISSING_PRICE"
    assert classification.tolist() == [ACCEPT, ACCEPT_WITH_QUALITY_FLAG, REJECT]


def test_core_commercial_predicate_is_centralized_and_exact() -> None:
    frame = pd.DataFrame(
        {
            "classification": [
                ACCEPT,
                ACCEPT_WITH_QUALITY_FLAG,
                ACCEPT,
                ACCEPT,
                ACCEPT,
                REJECT,
            ],
            "NONSTANDARD_INVOICE": [False, False, False, True, False, False],
            "is_cancelled": [False, False, True, False, False, False],
            "quantity": [1, 2, -1, 1, -1, 1],
            "unit_price": [
                Decimal("2.0000"),
                Decimal("3.0000"),
                Decimal("2.0000"),
                Decimal("2.0000"),
                Decimal("2.0000"),
                Decimal("2.0000"),
            ],
        }
    )

    assert core_commercial_sale_mask(frame).tolist() == [
        True,
        True,
        False,
        False,
        False,
        False,
    ]


def test_zero_price_never_enters_core_commercial_scope() -> None:
    frame = pd.DataFrame(
        {
            "classification": [ACCEPT_WITH_QUALITY_FLAG],
            "NONSTANDARD_INVOICE": [False],
            "is_cancelled": [False],
            "quantity": [10],
            "unit_price": [Decimal("0.0000")],
        }
    )
    assert not core_commercial_sale_mask(frame).iloc[0]


def test_core_cancellation_predicate_requires_standard_c_invoice() -> None:
    frame = pd.DataFrame(
        {
            "classification": [ACCEPT, ACCEPT, ACCEPT, ACCEPT, REJECT],
            "invoice_no": ["C123", "CABC", "C124", "C125", "C126"],
            "is_cancelled": [True, True, True, True, True],
            "quantity": [-1, -1, 1, -1, -1],
            "unit_price": [
                Decimal("2.0000"),
                Decimal("2.0000"),
                Decimal("2.0000"),
                Decimal("0.0000"),
                Decimal("2.0000"),
            ],
        }
    )

    assert core_cancellation_line_mask(frame).tolist() == [
        True,
        False,
        False,
        False,
        False,
    ]


def test_optional_missing_flags_do_not_block_an_otherwise_core_sale() -> None:
    frame = pd.DataFrame(
        {
            "classification": [ACCEPT_WITH_QUALITY_FLAG],
            "NONSTANDARD_INVOICE": [False],
            "is_cancelled": [False],
            "quantity": [1],
            "unit_price": [Decimal("1.0000")],
            "MISSING_CUSTOMER": [True],
            "MISSING_DESCRIPTION": [True],
            "MISSING_COUNTRY": [True],
        }
    )
    assert core_commercial_sale_mask(frame).iloc[0]

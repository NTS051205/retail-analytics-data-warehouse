"""Approved Phase 2 quality codes, classifications, and analytical predicates."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

import pandas as pd


ACCEPT = "ACCEPT"
ACCEPT_WITH_QUALITY_FLAG = "ACCEPT WITH QUALITY FLAG"
REJECT = "REJECT"
ACCEPTED_CLASSIFICATIONS = (ACCEPT, ACCEPT_WITH_QUALITY_FLAG)

CODE_DELIMITER = ";"

QUALITY_FLAG_ORDER = (
    "MISSING_CUSTOMER",
    "INVALID_CUSTOMER_ID",
    "MISSING_DESCRIPTION",
    "ZERO_PRICE",
    "QUANTITY_SIGN_MISMATCH",
    "NONSTANDARD_INVOICE",
    "MISSING_COUNTRY",
)

REJECT_REASON_ORDER = (
    "MISSING_INVOICE",
    "MISSING_STOCK_CODE",
    "INVALID_INVOICE_DATE",
    "MISSING_QUANTITY",
    "INVALID_QUANTITY",
    "MISSING_PRICE",
    "INVALID_PRICE",
    "NEGATIVE_PRICE_ADJUSTMENT",
)


def encode_ordered_codes(
    masks: Mapping[str, pd.Series],
    ordered_codes: Sequence[str],
    *,
    index: pd.Index,
) -> pd.Series:
    """Encode zero or more approved codes in one deterministic string."""
    result = pd.Series("", index=index, dtype="string")
    for code in ordered_codes:
        if code not in masks:
            continue
        selected = masks[code].reindex(index, fill_value=False).fillna(False).astype(bool)
        already_populated = selected & result.ne("")
        result.loc[already_populated] = (
            result.loc[already_populated] + CODE_DELIMITER
        )
        result.loc[selected] = result.loc[selected] + code
    return result


def classify_rows(reject_reason: pd.Series, quality_flags: pd.Series) -> pd.Series:
    """Apply REJECT > flagged accept > clean accept precedence."""
    if not reject_reason.index.equals(quality_flags.index):
        raise ValueError("Reject reasons and quality flags must use the same index.")

    classification = pd.Series(ACCEPT, index=reject_reason.index, dtype="string")
    classification.loc[quality_flags.fillna("").ne("")] = ACCEPT_WITH_QUALITY_FLAG
    classification.loc[reject_reason.fillna("").ne("")] = REJECT
    return classification


def _compare_decimal(value: Any, operator: str) -> bool:
    if value is None or value is pd.NA:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    try:
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception:
        return False
    if not decimal_value.is_finite():
        return False
    if operator == "positive":
        return decimal_value > 0
    if operator == "negative":
        return decimal_value < 0
    raise ValueError(f"Unsupported decimal comparison: {operator}")


def core_commercial_sale_mask(frame: pd.DataFrame) -> pd.Series:
    """Central implementation of the approved core commercial predicate."""
    accepted = frame["classification"].isin(ACCEPTED_CLASSIFICATIONS)
    standard_invoice = ~frame["NONSTANDARD_INVOICE"].fillna(False).astype(bool)
    not_cancelled = ~frame["is_cancelled"].fillna(False).astype(bool)
    positive_quantity = pd.to_numeric(frame["quantity"], errors="coerce").gt(0)
    positive_price = frame["unit_price"].map(
        lambda value: _compare_decimal(value, "positive")
    )
    return (
        accepted
        & standard_invoice
        & not_cancelled
        & positive_quantity
        & positive_price
    ).fillna(False)


def core_cancellation_line_mask(frame: pd.DataFrame) -> pd.Series:
    """Central implementation of the approved core cancellation predicate."""
    accepted = frame["classification"].isin(ACCEPTED_CLASSIFICATIONS)
    standard_cancellation = frame["invoice_no"].astype("string").str.fullmatch(
        r"C[0-9]+", na=False
    )
    cancelled = frame["is_cancelled"].fillna(False).astype(bool)
    negative_quantity = pd.to_numeric(frame["quantity"], errors="coerce").lt(0)
    positive_price = frame["unit_price"].map(
        lambda value: _compare_decimal(value, "positive")
    )
    return (
        accepted
        & standard_cancellation
        & cancelled
        & negative_quantity
        & positive_price
    ).fillna(False)

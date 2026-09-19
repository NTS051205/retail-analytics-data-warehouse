"""Shared constants and small helpers for the Phase 1 profiler."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import pandas as pd
from pandas.api.types import is_object_dtype, is_string_dtype


EXPECTED_COLUMNS = [
    "Invoice",
    "StockCode",
    "Description",
    "Quantity",
    "InvoiceDate",
    "Price",
    "Customer ID",
    "Country",
]

BUSINESS_COLUMNS = EXPECTED_COLUMNS.copy()
SAMPLE_SIZE = 20
DESCRIPTION_EXAMPLE_LIMIT = 20
TOP_VALUE_LIMIT = 10

CSV_OUTPUT_NAMES = [
    "dataset_summary.csv",
    "sheet_summary.csv",
    "null_summary.csv",
    "cardinality_summary.csv",
    "cancellation_summary.csv",
    "numeric_summary.csv",
    "country_summary.csv",
    "duplicate_summary.csv",
    "stockcode_summary.csv",
    "description_consistency_summary.csv",
    "extreme_quantity_samples.csv",
    "price_anomaly_samples.csv",
]

DUPLICATE_DISCLAIMER = (
    "Duplicate-looking rows are measured only and are not assumed to be "
    "accidental duplicates because the source does not provide a trustworthy "
    "invoice-line identifier."
)

REQUIRED_REPORT_HEADINGS = [
    "## Dataset Overview",
    "## Sheet Structure",
    "## Schema",
    "## Date Range",
    "## Null Patterns",
    "## Invoice / Cancellation Patterns",
    "## Quantity Patterns",
    "## Price Patterns",
    "## Customer ID Findings",
    "## Product / StockCode Findings",
    "## Country Findings",
    "## Duplicate-Looking Rows",
    "## Important Anomalies",
    "## Open Questions for Phase 2",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def percentage(numerator: int | float, denominator: int | float) -> float:
    return 0.0 if denominator == 0 else float(numerator) / float(denominator) * 100.0


def blank_mask(series: pd.Series) -> pd.Series:
    """Identify non-null strings containing no non-space text."""
    if not (is_object_dtype(series.dtype) or is_string_dtype(series.dtype)):
        return pd.Series(False, index=series.index, dtype=bool)
    return (series.notna() & series.astype("string").str.strip().eq("")).fillna(False)


def derived_numeric(series: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return parsed values, blank-string mask, and nonblank parse failures."""
    blanks = blank_mask(series)
    numeric = pd.to_numeric(series.mask(blanks), errors="coerce")
    invalid = (series.notna() & ~blanks & numeric.isna()).fillna(False)
    return numeric, blanks, invalid


def safe_scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat(sep=" ")
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        missing = False
    if isinstance(missing, bool) and missing:
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except (AttributeError, ValueError):
            pass
    return value


def as_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=safe_scalar)


def top_values(series: pd.Series, limit: int = TOP_VALUE_LIMIT) -> list[dict[str, Any]]:
    return [
        {"value": safe_scalar(value), "row_count": int(count)}
        for value, count in series.dropna().value_counts(dropna=False).head(limit).items()
    ]


def format_date(value: Any) -> str:
    return "" if value is None or pd.isna(value) else pd.Timestamp(value).isoformat(sep=" ")


def markdown_table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    def cell(value: Any) -> str:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return ""
        return str(value).replace("|", "\\|").replace("\n", " ")

    output = [
        "| " + " | ".join(cell(header) for header in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    output.extend("| " + " | ".join(cell(value) for value in row) + " |" for row in rows)
    return "\n".join(output)


def null_records(
    frame: pd.DataFrame,
    columns: Sequence[Any],
    scope: str,
    sheet_name: str,
) -> list[dict[str, Any]]:
    row_count = len(frame)
    records = []
    for column in columns:
        null_count = int(frame[column].isna().sum())
        blank_count = int(blank_mask(frame[column]).sum())
        records.append(
            {
                "scope": scope,
                "sheet_name": sheet_name,
                "column_name": str(column),
                "row_count": row_count,
                "null_count": null_count,
                "null_percentage": percentage(null_count, row_count),
                "blank_count": blank_count,
                "blank_percentage": percentage(blank_count, row_count),
            }
        )
    return records


def numeric_summary_record(column: str, series: pd.Series) -> dict[str, Any]:
    numeric, blanks, invalid = derived_numeric(series)
    valid = numeric.dropna()
    quantiles = valid.quantile([0.01, 0.05, 0.25, 0.50, 0.75, 0.95, 0.99])
    record = {
        "scope": "combined",
        "column_name": column,
        "count": int(valid.count()),
        "null_count": int(series.isna().sum()),
        "blank_count": int(blanks.sum()),
        "invalid_numeric_count": int(invalid.sum()),
        "negative_count": int(valid.lt(0).sum()),
        "zero_count": int(valid.eq(0).sum()),
        "positive_count": int(valid.gt(0).sum()),
        "mean": valid.mean(),
        "median": valid.median(),
        "std": valid.std(ddof=1),
        "min": valid.min(),
        **{f"p{int(q * 100):02d}": quantiles.get(q) for q in quantiles.index},
        "max": valid.max(),
    }
    signs = record["negative_count"] + record["zero_count"] + record["positive_count"]
    if signs != record["count"]:
        raise AssertionError(f"Numeric sign counts do not reconcile for {column}.")
    return record


def sample_rows(frame: pd.DataFrame, indices: Sequence[Any], sample_type: str) -> pd.DataFrame:
    columns = ["source_sheet", "source_row_number", *BUSINESS_COLUMNS]
    sample = frame.loc[list(indices), columns].copy()
    sample.insert(0, "sample_rank", range(1, len(sample) + 1))
    sample.insert(0, "sample_type", sample_type)
    return sample

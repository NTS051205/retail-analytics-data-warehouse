"""StockCode and description profiling for Phase 1."""

from __future__ import annotations

from typing import Any

import pandas as pd

from .common import DESCRIPTION_EXAMPLE_LIMIT, as_json, safe_scalar, top_values


def profile_stockcodes(
    masks: dict[str, Any],
    combined_rows: int,
) -> pd.DataFrame:
    stockcode = masks["stockcode"]
    text = stockcode.astype("string")
    stripped = text.str.strip()
    valid = stockcode.notna() & ~masks["stockcode_blank"]

    numeric_like = (valid & stripped.str.fullmatch(r"\d+(?:\.0+)?", na=False)).fillna(False)
    alphabetic = (valid & stripped.str.fullmatch(r"[A-Za-z]+", na=False)).fillna(False)
    mixed = (
        valid
        & stripped.str.fullmatch(r"(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9]+", na=False)
    ).fillna(False)
    special = (valid & ~(numeric_like | alphabetic | mixed)).fillna(False)

    categories = {
        "null": masks["stockcode_null"],
        "blank": masks["stockcode_blank"],
        "numeric_like": numeric_like,
        "alphabetic": alphabetic,
        "mixed_alphanumeric": mixed,
        "special_or_other": special,
    }
    records = [
        {
            "record_type": "overall",
            "category": "all_rows",
            "row_count": combined_rows,
            "distinct_stockcodes": int(stockcode.nunique(dropna=True)),
            "stock_code": None,
            "top_examples_json": as_json(top_values(stockcode)),
        }
    ]
    for category, selected in categories.items():
        records.append(
            {
                "record_type": "classification",
                "category": category,
                "row_count": int(selected.sum()),
                "distinct_stockcodes": int(stockcode.loc[selected].nunique(dropna=True)),
                "stock_code": None,
                "top_examples_json": as_json(top_values(stockcode.loc[selected])),
            }
        )

    conventional = stripped.str.fullmatch(r"\d{5}[A-Za-z]?", na=False)
    unusual_counts = text.loc[(valid & ~conventional).fillna(False)].value_counts().head(30)
    for code, count in unusual_counts.items():
        records.append(
            {
                "record_type": "non_standard_pattern_example",
                "category": "does_not_match_5_digits_optional_letter",
                "row_count": int(count),
                "distinct_stockcodes": 1,
                "stock_code": safe_scalar(code),
                "top_examples_json": "",
            }
        )
    return pd.DataFrame(records)


def profile_descriptions(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    description_counts = frame.groupby("StockCode", dropna=True, sort=False)[
        "Description"
    ].nunique(dropna=True)
    row_counts = frame.groupby("StockCode", dropna=True, sort=False).size()
    stats = {
        "one_description": int(description_counts.eq(1).sum()),
        "multiple_descriptions": int(description_counts.gt(1).sum()),
        "zero_descriptions": int(description_counts.eq(0).sum()),
        "maximum_descriptions": int(description_counts.max()) if not description_counts.empty else 0,
    }

    records: list[dict[str, Any]] = [
        {
            "record_type": "summary",
            "metric": "stockcodes_with_one_distinct_non_null_description",
            "value": stats["one_description"],
            "stock_code": None,
            "distinct_non_null_description_count": None,
            "row_count": None,
            "descriptions_sample_json": "",
        },
        {
            "record_type": "summary",
            "metric": "stockcodes_with_multiple_distinct_non_null_descriptions",
            "value": stats["multiple_descriptions"],
            "stock_code": None,
            "distinct_non_null_description_count": None,
            "row_count": None,
            "descriptions_sample_json": "",
        },
        {
            "record_type": "summary",
            "metric": "stockcodes_with_zero_non_null_descriptions",
            "value": stats["zero_descriptions"],
            "stock_code": None,
            "distinct_non_null_description_count": None,
            "row_count": None,
            "descriptions_sample_json": "",
        },
        {
            "record_type": "summary",
            "metric": "maximum_distinct_non_null_descriptions_for_one_stockcode",
            "value": stats["maximum_descriptions"],
            "stock_code": None,
            "distinct_non_null_description_count": None,
            "row_count": None,
            "descriptions_sample_json": "",
        },
    ]

    candidates = pd.DataFrame(
        {
            "distinct_non_null_description_count": description_counts,
            "row_count": row_counts,
        }
    )
    candidates = candidates.loc[
        candidates["distinct_non_null_description_count"].gt(1)
    ].sort_values(
        ["distinct_non_null_description_count", "row_count"],
        ascending=[False, False],
        kind="stable",
    )

    examples = []
    for code, row in candidates.head(DESCRIPTION_EXAMPLE_LIMIT).iterrows():
        record = {
            "record_type": "example",
            "metric": "",
            "value": None,
            "stock_code": safe_scalar(code),
            "distinct_non_null_description_count": int(
                row["distinct_non_null_description_count"]
            ),
            "row_count": int(row["row_count"]),
            "descriptions_sample_json": as_json(
                top_values(frame.loc[frame["StockCode"].eq(code), "Description"], 8)
            ),
        }
        records.append(record)
        examples.append(record)

    return pd.DataFrame(records), pd.DataFrame(examples), stats

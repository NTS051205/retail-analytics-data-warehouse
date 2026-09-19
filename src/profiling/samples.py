"""Duplicate-looking groups and deterministic anomaly samples."""

from __future__ import annotations

from typing import Any

import pandas as pd

from .common import BUSINESS_COLUMNS, SAMPLE_SIZE, sample_rows


def profile_duplicates(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    participating = frame.duplicated(subset=BUSINESS_COLUMNS, keep=False)
    participating_rows = int(participating.sum())

    if participating_rows:
        examples = (
            frame.loc[participating, BUSINESS_COLUMNS]
            .groupby(BUSINESS_COLUMNS, dropna=False, sort=False)
            .size()
            .rename("group_size")
            .reset_index()
            .sort_values("group_size", ascending=False, kind="stable")
        )
        group_count = len(examples)
        largest_group = int(examples["group_size"].max())
        examples = examples.head(10).reset_index(drop=True)
        examples.insert(0, "example_rank", range(1, len(examples) + 1))
    else:
        group_count = largest_group = 0
        examples = pd.DataFrame(columns=["example_rank", *BUSINESS_COLUMNS, "group_size"])

    stats = {
        "participating_rows": participating_rows,
        "group_count": group_count,
        "largest_group_size": largest_group,
    }
    records: list[dict[str, Any]] = [
        {
            "record_type": "summary",
            "metric": "rows_participating_in_duplicate_looking_groups",
            "value": participating_rows,
        },
        {
            "record_type": "summary",
            "metric": "duplicate_looking_group_count",
            "value": group_count,
        },
        {
            "record_type": "summary",
            "metric": "largest_duplicate_looking_group_size",
            "value": largest_group,
        },
    ]
    records.extend(
        {
            "record_type": "example",
            "metric": "representative_duplicate_looking_group",
            "value": None,
            **row,
        }
        for row in examples.to_dict(orient="records")
    )
    return pd.DataFrame(records), examples, stats


def build_samples(
    frame: pd.DataFrame,
    masks: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    quantity = masks["quantity"]
    negative_quantity = (
        quantity.loc[masks["quantity_masks"]["negative"]]
        .sort_values(ascending=True, kind="stable")
        .head(SAMPLE_SIZE)
        .index
    )
    positive_quantity = (
        quantity.loc[masks["quantity_masks"]["positive"]]
        .sort_values(ascending=False, kind="stable")
        .head(SAMPLE_SIZE)
        .index
    )
    quantity_samples = pd.concat(
        [
            sample_rows(frame, negative_quantity, "most_negative_quantity"),
            sample_rows(frame, positive_quantity, "largest_positive_quantity"),
        ],
        ignore_index=True,
    )

    price = masks["price"]
    negative_price = (
        price.loc[masks["negative_price"]]
        .sort_values(ascending=True, kind="stable")
        .head(SAMPLE_SIZE)
        .index
    )
    zero_price = price.loc[masks["zero_price"]].head(SAMPLE_SIZE).index
    positive_price = (
        price.loc[price.gt(0)]
        .sort_values(ascending=False, kind="stable")
        .head(SAMPLE_SIZE)
        .index
    )
    price_samples = pd.concat(
        [
            sample_rows(frame, negative_price, "negative_price"),
            sample_rows(frame, zero_price, "zero_price"),
            sample_rows(frame, positive_price, "largest_positive_price"),
        ],
        ignore_index=True,
    )
    return quantity_samples, price_samples

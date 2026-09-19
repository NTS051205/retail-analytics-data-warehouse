"""Assemble Phase 1 metrics into the contracted CSV outputs."""

from __future__ import annotations

from os import stat_result
from typing import Any

import pandas as pd

from ..config import RAW_DATA_PATH
from .common import (
    as_json,
    numeric_summary_record,
    percentage,
    safe_scalar,
    sha256,
)
from .metrics import (
    build_masks,
    profile_cancellations,
    profile_cardinality,
    profile_countries,
    profile_dates,
)
from .products import profile_descriptions, profile_stockcodes
from .samples import build_samples, profile_duplicates


def _build_dataset_summary(analysis: dict[str, Any]) -> pd.DataFrame:
    source = analysis["source"]
    frame = source["frame"]
    masks = analysis["masks"]
    dates = analysis["dates"]
    descriptions = analysis["description_stats"]
    duplicates = analysis["duplicate_stats"]
    country_summary = analysis["country_summary"]
    combined_rows = len(frame)
    records: list[dict[str, Any]] = []

    def add(section: str, metric: str, value: Any, definition: str = "") -> None:
        records.append(
            {
                "section": section,
                "metric": metric,
                "value": safe_scalar(value),
                "definition": definition,
            }
        )

    add("workbook", "file_name", RAW_DATA_PATH.name)
    add("workbook", "size_bytes", analysis["stat_before"].st_size)
    add("workbook", "sha256_before", analysis["hash_before"])
    add("workbook", "sha256_after_analysis", analysis["hash_after_analysis"])
    add("workbook", "sheet_count", len(source["sheet_names"]))
    add("workbook", "sheet_names_json", as_json(source["sheet_names"]))
    add("rows", "sum_sheet_row_counts", int(source["sheet_summary"]["row_count"].sum()))
    add("rows", "combined_row_count", combined_rows)
    add(
        "rows",
        "sheet_combined_row_count_reconciled",
        int(source["sheet_summary"]["row_count"].sum()) == combined_rows,
    )
    add(
        "schema",
        "schemas_identical",
        bool(source["sheet_summary"]["schema_matches_reference"].all()),
    )
    for column in source["source_columns"]:
        add("schema", f"combined_dtype::{column}", str(frame[column].dtype))

    add("dates", "minimum_valid_invoice_datetime", dates["minimum"])
    add("dates", "maximum_valid_invoice_datetime", dates["maximum"])
    add("dates", "invoice_date_null_count", dates["null_count"])
    add("dates", "invoice_date_blank_count", dates["blank_count"])
    add("dates", "invoice_date_invalid_nonblank_count", dates["invalid_count"])
    add("dates", "common_sheet_date_overlap_start", dates["overlap_start"])
    add("dates", "common_sheet_date_overlap_end", dates["overlap_end"])
    for value, count in dates["rows_by_year"]:
        add("rows_by_year", value, count)
    for value, count in dates["rows_by_month"]:
        add("rows_by_year_month", value, count)

    invoice = masks["invoice"]
    add("invoice", "total_rows", combined_rows)
    add("invoice", "distinct_non_null_invoices", int(invoice.nunique(dropna=True)))
    add("invoice", "c_prefixed_rows", int(masks["c_invoice"].sum()))
    add(
        "invoice",
        "c_prefixed_distinct_invoices",
        int(invoice.loc[masks["c_invoice"]].nunique(dropna=True)),
    )
    add("invoice", "non_c_nonblank_rows", int(masks["non_c_invoice"].sum()))
    add(
        "invoice",
        "non_c_nonblank_distinct_invoices",
        int(invoice.loc[masks["non_c_invoice"]].nunique(dropna=True)),
    )
    add("invoice", "null_rows", int(masks["invoice_null"].sum()))
    add("invoice", "blank_rows", int(masks["invoice_blank"].sum()))
    add("invoice_format", "unexpected_nonblank_rows", int(masks["unexpected_invoice"].sum()))

    customer_missing = masks["customer_missing"]
    add("customer_id", "combined_dtype", str(masks["customer"].dtype))
    add("customer_id", "string_rendering_ends_with_dot_zero_rows", int(masks["customer_dot_zero"].sum()))
    add("customer_id", "fractional_numeric_rows", int(masks["customer_fractional"].sum()))
    add("customer_id", "invalid_numeric_rows", int(masks["customer_invalid"].sum()))
    add("missing_customer", "rows", int(customer_missing.sum()))
    add("missing_customer", "percentage", percentage(int(customer_missing.sum()), combined_rows))
    add(
        "missing_customer",
        "distinct_invoices",
        int(invoice.loc[customer_missing].nunique(dropna=True)),
    )
    add("missing_customer", "c_prefixed_rows", int((customer_missing & masks["c_invoice"]).sum()))
    add("missing_customer", "non_c_rows", int((customer_missing & masks["non_c_invoice"]).sum()))
    add(
        "missing_customer",
        "negative_quantity_rows",
        int((customer_missing & masks["quantity_masks"]["negative"]).sum()),
    )
    add("missing_customer", "zero_price_rows", int((customer_missing & masks["zero_price"]).sum()))

    missing_description = masks["description_missing"]
    add("missing_description", "rows", int(missing_description.sum()))
    add(
        "missing_description",
        "percentage",
        percentage(int(missing_description.sum()), combined_rows),
    )
    add(
        "missing_description",
        "missing_customer_overlap_rows",
        int((missing_description & customer_missing).sum()),
    )
    add("missing_description", "c_prefixed_rows", int((missing_description & masks["c_invoice"]).sum()))
    add("missing_description", "non_c_rows", int((missing_description & masks["non_c_invoice"]).sum()))
    for category, selected in masks["quantity_masks"].items():
        add("missing_description_quantity", category, int((missing_description & selected).sum()))
    for category, selected in {
        "negative": masks["price"].lt(0),
        "zero": masks["price"].eq(0),
        "positive": masks["price"].gt(0),
        "null_blank_or_invalid": masks["price"].isna(),
    }.items():
        add("missing_description_price", category, int((missing_description & selected).sum()))

    add("price", "negative_rows", int(masks["negative_price"].sum()))
    add("price", "zero_rows", int(masks["zero_price"].sum()))
    add(
        "zero_price",
        "distinct_invoices",
        int(invoice.loc[masks["zero_price"]].nunique(dropna=True)),
    )
    for category, selected in {
        "c_prefixed_rows": masks["c_invoice"],
        "non_c_rows": masks["non_c_invoice"],
        "invoice_null_rows": masks["invoice_null"],
        "missing_customer_rows": customer_missing,
    }.items():
        add("zero_price", category, int((masks["zero_price"] & selected).sum()))
    add("negative_price", "c_prefixed_rows", int((masks["negative_price"] & masks["c_invoice"]).sum()))
    add("negative_price", "non_c_rows", int((masks["negative_price"] & masks["non_c_invoice"]).sum()))
    for category, selected in masks["quantity_masks"].items():
        add("negative_price_quantity", category, int((masks["negative_price"] & selected).sum()))

    add("country", "null_rows", int(masks["country_null"].sum()))
    add("country", "blank_rows", int(masks["country_blank"].sum()))
    add(
        "country",
        "outer_whitespace_rows",
        int(country_summary.loc[country_summary["has_outer_whitespace"], "row_count"].sum()),
    )
    add("country", "comparison_key_variant_group_count", len(analysis["country_variants"]))
    add("stockcode", "null_rows", int(masks["stockcode_null"].sum()))
    add("stockcode", "blank_rows", int(masks["stockcode_blank"].sum()))
    add("description", "null_rows", int(masks["description_missing"].sum()))
    add("description", "blank_rows", int(masks["description_blank"].sum()))
    add("description_consistency", "stockcodes_with_one_description", descriptions["one_description"])
    add(
        "description_consistency",
        "stockcodes_with_multiple_descriptions",
        descriptions["multiple_descriptions"],
    )
    add("description_consistency", "maximum_descriptions_per_stockcode", descriptions["maximum_descriptions"])
    add("duplicates", "rows_participating", duplicates["participating_rows"])
    add("duplicates", "group_count", duplicates["group_count"])
    add("duplicates", "largest_group_size", duplicates["largest_group_size"])
    return pd.DataFrame(records)


def build_profiles(
    source: dict[str, Any],
    stat_before: stat_result,
    hash_before: str,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    frame = source["frame"]
    combined_rows = len(frame)
    masks = build_masks(frame)
    dates = profile_dates(masks, source["sheet_summary"])

    print("[profile] Building aggregate tables", flush=True)
    cardinality_summary = profile_cardinality(frame, masks)
    cancellation_summary = profile_cancellations(masks, combined_rows)
    numeric_summary = pd.DataFrame(
        [
            numeric_summary_record("Quantity", frame["Quantity"]),
            numeric_summary_record("Price", frame["Price"]),
        ]
    )
    country_summary, country_variants = profile_countries(masks, combined_rows)
    stockcode_summary = profile_stockcodes(masks, combined_rows)
    description_summary, description_examples, description_stats = profile_descriptions(frame)
    duplicate_summary, duplicate_examples, duplicate_stats = profile_duplicates(frame)
    quantity_samples, price_samples = build_samples(frame, masks)

    hash_after_analysis = sha256(RAW_DATA_PATH)
    if hash_after_analysis != hash_before:
        raise AssertionError("The source workbook hash changed during profiling.")

    analysis = {
        "source": source,
        "stat_before": stat_before,
        "hash_before": hash_before,
        "hash_after_analysis": hash_after_analysis,
        "masks": masks,
        "dates": dates,
        "country_summary": country_summary,
        "country_variants": country_variants,
        "stockcode_summary": stockcode_summary,
        "description_examples": description_examples,
        "description_stats": description_stats,
        "duplicate_examples": duplicate_examples,
        "duplicate_stats": duplicate_stats,
    }
    dataset_summary = _build_dataset_summary(analysis)
    outputs = {
        "dataset_summary.csv": dataset_summary,
        "sheet_summary.csv": source["sheet_summary"],
        "null_summary.csv": source["null_summary"],
        "cardinality_summary.csv": cardinality_summary,
        "cancellation_summary.csv": cancellation_summary,
        "numeric_summary.csv": numeric_summary,
        "country_summary.csv": country_summary,
        "duplicate_summary.csv": duplicate_summary,
        "stockcode_summary.csv": stockcode_summary,
        "description_consistency_summary.csv": description_summary,
        "extreme_quantity_samples.csv": quantity_samples,
        "price_anomaly_samples.csv": price_samples,
    }
    analysis["outputs"] = outputs
    return outputs, analysis

"""Render the Phase 1 Markdown profiling report from computed metrics."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from .common import (
    DUPLICATE_DISCLAIMER,
    EXPECTED_COLUMNS,
    as_json,
    markdown_table,
    percentage,
    top_values,
)


def _integer(value: int | float) -> str:
    return f"{int(value):,}"


def _decimal(value: Any, digits: int = 4) -> str:
    return "n/a" if value is None or pd.isna(value) else f"{float(value):,.{digits}f}"


def _percent(value: Any) -> str:
    return "n/a" if value is None or pd.isna(value) else f"{float(value):.4f}%"


def _section(
    title: str,
    facts: list[str],
    interpretation: str | None,
) -> list[str]:
    lines = [f"## {title}", "", "**OBSERVED FACT**", "", *facts, ""]
    if interpretation:
        lines.extend(["**POSSIBLE INTERPRETATION**", "", interpretation, ""])
    return lines


def build_report(analysis: dict[str, Any]) -> str:
    source = analysis["source"]
    frame = source["frame"]
    outputs = analysis["outputs"]
    masks = analysis["masks"]
    dates = analysis["dates"]
    descriptions = analysis["description_stats"]
    duplicates = analysis["duplicate_stats"]
    combined_rows = len(frame)

    sheet_summary = outputs["sheet_summary.csv"]
    null_summary = outputs["null_summary.csv"]
    cancellation_summary = outputs["cancellation_summary.csv"]
    numeric_summary = outputs["numeric_summary.csv"]
    country_summary = outputs["country_summary.csv"]
    stockcode_summary = outputs["stockcode_summary.csv"]

    sheet_table = markdown_table(
        ["Sheet", "Rows", "Columns", "Minimum InvoiceDate", "Maximum InvoiceDate", "Schema matches"],
        (
            (
                row.sheet_name,
                _integer(row.row_count),
                row.column_count,
                row.min_invoice_datetime,
                row.max_invoice_datetime,
                row.schema_matches_reference,
            )
            for row in sheet_summary.itertuples(index=False)
        ),
    )

    dtype_rows = []
    for row in sheet_summary.itertuples(index=False):
        dtype_rows.extend(
            (row.sheet_name, column, dtype)
            for column, dtype in json.loads(row.pandas_dtypes_json).items()
        )
    dtype_rows.extend(
        ("Combined", column, str(frame[column].dtype)) for column in source["source_columns"]
    )
    dtype_table = markdown_table(["Scope", "Column", "Pandas dtype"], dtype_rows)

    combined_nulls = null_summary.loc[null_summary["scope"].eq("combined")]
    null_table = markdown_table(
        ["Column", "Null rows", "Null %", "Blank-string rows", "Blank %"],
        (
            (
                row.column_name,
                _integer(row.null_count),
                _percent(row.null_percentage),
                _integer(row.blank_count),
                _percent(row.blank_percentage),
            )
            for row in combined_nulls.itertuples(index=False)
        ),
    )

    cancellation_rows = cancellation_summary.loc[
        cancellation_summary["invoice_class"].isin(["c_prefixed", "non_c"])
        & cancellation_summary["quantity_class"].isin(["negative", "zero", "positive"])
    ]
    cancellation_table = markdown_table(
        ["Invoice class", "Quantity class", "Rows", "Distinct invoices"],
        (
            (
                row.invoice_class,
                row.quantity_class,
                _integer(row.row_count),
                _integer(row.distinct_invoice_count),
            )
            for row in cancellation_rows.itertuples(index=False)
        ),
    )

    quantity_row = numeric_summary.loc[numeric_summary["column_name"].eq("Quantity")].iloc[0]
    price_row = numeric_summary.loc[numeric_summary["column_name"].eq("Price")].iloc[0]
    numeric_table = markdown_table(
        ["Metric", "Quantity", "Price"],
        (
            (metric, _decimal(quantity_row[metric]), _decimal(price_row[metric]))
            for metric in [
                "count",
                "null_count",
                "negative_count",
                "zero_count",
                "positive_count",
                "mean",
                "median",
                "std",
                "min",
                "p01",
                "p05",
                "p25",
                "p50",
                "p75",
                "p95",
                "p99",
                "max",
            ]
        ),
    )

    year_table = markdown_table(
        ["Year", "Rows"],
        ((year, _integer(count)) for year, count in dates["rows_by_year"]),
    )
    month_table = markdown_table(
        ["Year-month", "Rows"],
        ((month, _integer(count)) for month, count in dates["rows_by_month"]),
    )
    country_table = markdown_table(
        ["Country", "Rows", "Row %", "Distinct invoices", "Known customers", "Missing-customer rows"],
        (
            (
                row.country_value if row.value_status == "value" else f"<{row.value_status.upper()}>",
                _integer(row.row_count),
                _percent(row.row_percentage),
                _integer(row.distinct_invoices),
                _integer(row.known_customers),
                _integer(row.missing_customer_rows),
            )
            for row in country_summary.head(15).itertuples(index=False)
        ),
    )
    stock_table = markdown_table(
        ["Structural category", "Rows", "Distinct values", "Frequent examples"],
        (
            (
                row.category,
                _integer(row.row_count),
                _integer(row.distinct_stockcodes),
                row.top_examples_json,
            )
            for row in stockcode_summary.loc[
                stockcode_summary["record_type"].eq("classification")
            ].itertuples(index=False)
        ),
    )
    description_table = markdown_table(
        ["StockCode", "Distinct non-null descriptions", "Rows", "Description samples"],
        (
            (
                row.stock_code,
                row.distinct_non_null_description_count,
                _integer(row.row_count),
                row.descriptions_sample_json,
            )
            for row in analysis["description_examples"].head(10).itertuples(index=False)
        ),
    )
    duplicate_table = markdown_table(
        ["Rank", "Group size", *EXPECTED_COLUMNS],
        (
            (
                row["example_rank"],
                _integer(row["group_size"]),
                *(row[column] for column in EXPECTED_COLUMNS),
            )
            for row in analysis["duplicate_examples"].head(10).to_dict(orient="records")
        ),
    )

    invoice = masks["invoice"]
    customer_missing = masks["customer_missing"]
    missing_description = masks["description_missing"]
    negative_price = masks["negative_price"]
    zero_price = masks["zero_price"]
    quantities = masks["quantity_masks"]
    prices = masks["price"]

    lines = [
        "# Online Retail II Data Profiling",
        "",
        "This report records Phase 1 observations from the unchanged source workbook. It does not define cleaning rules or remove records.",
        "",
    ]
    lines += _section(
        "Dataset Overview",
        [
            f"- Workbook: `data/raw/online_retail_II.xlsx` ({_integer(analysis['stat_before'].st_size)} bytes).",
            f"- SHA-256 before and after analysis: `{analysis['hash_before']}`.",
            f"- Worksheets read exactly once: {_integer(len(source['sheet_names']))} (`{', '.join(source['sheet_names'])}`).",
            f"- Combined source rows: {_integer(combined_rows)}.",
            f"- Sum of worksheet rows equals the combined row count: `{int(sheet_summary['row_count'].sum()) == combined_rows}`.",
            "- `source_row_number` is the physical Excel row number; row 1 is the header.",
            "- The combined dataframe exists only in memory. No production row identifier or transformed dataset is created.",
        ],
        "- The workbook is suitable for evidence collection, but profiling findings alone do not authorize cleaning or exclusion rules.",
    )
    lines += _section(
        "Sheet Structure",
        [sheet_table, f"- All worksheets use the same ordered schema: `{bool(sheet_summary['schema_matches_reference'].all())}`."],
        "- Matching schemas allow combined profiling without renaming source columns.",
    )
    lines += _section(
        "Schema",
        [
            f"- Actual ordered columns: `{as_json([str(value) for value in source['source_columns']])}`.",
            f"- Missing expected columns: `{as_json([column for column in EXPECTED_COLUMNS if column not in source['source_columns']])}`.",
            f"- Extra columns: `{as_json([str(column) for column in source['source_columns'] if column not in EXPECTED_COLUMNS])}`.",
            f"- Headers with outer whitespace: `{as_json([str(column) for column in source['source_columns'] if str(column) != str(column).strip()])}`.",
            dtype_table,
        ],
        "- Pandas dtype inference is evidence about source representation, not a final warehouse type decision.",
    )
    lines += _section(
        "Date Range",
        [
            f"- Minimum valid InvoiceDate: `{dates['minimum']}`.",
            f"- Maximum valid InvoiceDate: `{dates['maximum']}`.",
            f"- True-null rows: {_integer(dates['null_count'])}; blank-string rows: {_integer(dates['blank_count'])}; unparseable nonblank rows: {_integer(dates['invalid_count'])}.",
            f"- Common overlap across worksheet ranges: `{dates['overlap_start']}` to `{dates['overlap_end']}`.",
            "- Missing or unparseable dates are excluded from the period tables.",
            year_table,
            month_table,
        ],
        "- The observed period coverage can support monthly replay after Phase 2 establishes treatment rules.",
    )
    lines += _section(
        "Null Patterns",
        [null_table],
        "- Nulls and blank strings remain separate observations because their source meanings may differ.",
    )
    lines += _section(
        "Invoice / Cancellation Patterns",
        [
            f"- Distinct non-null invoices: {_integer(invoice.nunique(dropna=True))}.",
            f"- C-prefixed rows: {_integer(masks['c_invoice'].sum())}; distinct invoices: {_integer(invoice.loc[masks['c_invoice']].nunique(dropna=True))}.",
            f"- Non-C nonblank rows: {_integer(masks['non_c_invoice'].sum())}; distinct invoices: {_integer(invoice.loc[masks['non_c_invoice']].nunique(dropna=True))}.",
            f"- Null invoice rows: {_integer(masks['invoice_null'].sum())}; blank-string rows: {_integer(masks['invoice_blank'].sum())}.",
            f"- Unexpected nonblank invoice formats: {_integer(masks['unexpected_invoice'].sum())}; frequent examples: `{as_json(top_values(invoice.loc[masks['unexpected_invoice']]))}`.",
            cancellation_table,
        ],
        "- C-prefixed positive quantities and non-C negative quantities require review before treatment rules are defined.",
    )
    lines += _section(
        "Quantity Patterns",
        [
            numeric_table,
            "- Percentiles use Pandas linear interpolation; standard deviation uses `ddof=1`.",
            "- `extreme_quantity_samples.csv` contains deterministic small samples, not invalid-row classifications.",
        ],
        "- Extreme quantities may represent bulk activity, cancellations, adjustments, or source issues.",
    )
    lines += _section(
        "Price Patterns",
        [
            f"- Negative-price rows: {_integer(negative_price.sum())}; zero-price rows: {_integer(zero_price.sum())}.",
            f"- Zero-price invoice relationship: C-prefixed {_integer((zero_price & masks['c_invoice']).sum())}, non-C {_integer((zero_price & masks['non_c_invoice']).sum())}, null invoice {_integer((zero_price & masks['invoice_null']).sum())}.",
            f"- Zero-price rows with missing Customer ID: {_integer((zero_price & customer_missing).sum())}.",
            f"- Frequent zero-price descriptions: `{as_json(top_values(masks['description'].loc[zero_price]))}`.",
            f"- Frequent zero-price StockCodes: `{as_json(top_values(masks['stockcode'].loc[zero_price]))}`.",
            f"- Frequent negative-price descriptions: `{as_json(top_values(masks['description'].loc[negative_price]))}`.",
            f"- Negative-price invoice relationship: C-prefixed {_integer((negative_price & masks['c_invoice']).sum())}, non-C {_integer((negative_price & masks['non_c_invoice']).sum())}, null invoice {_integer((negative_price & masks['invoice_null']).sum())}.",
            f"- Negative-price quantity relationship: negative {_integer((negative_price & quantities['negative']).sum())}, zero {_integer((negative_price & quantities['zero']).sum())}, positive {_integer((negative_price & quantities['positive']).sum())}, null/blank/unparseable {_integer((negative_price & quantities['null_blank_or_invalid']).sum())}.",
            "- `price_anomaly_samples.csv` contains samples for negative, zero, and largest positive prices.",
        ],
        "- Zero and negative prices may represent different source situations; no final interpretation is assigned here.",
    )
    lines += _section(
        "Customer ID Findings",
        [
            f"- Combined Pandas dtype: `{masks['customer'].dtype}`.",
            f"- Values rendered with a `.0` suffix: {_integer(masks['customer_dot_zero'].sum())}; values with a non-zero fractional component: {_integer(masks['customer_fractional'].sum())}.",
            f"- Missing Customer ID rows: {_integer(customer_missing.sum())} ({_percent(percentage(int(customer_missing.sum()), combined_rows))}).",
            f"- Distinct invoices among missing-customer rows: {_integer(invoice.loc[customer_missing].nunique(dropna=True))}.",
            f"- Missing-customer relationship: C-prefixed {_integer((customer_missing & masks['c_invoice']).sum())}, non-C {_integer((customer_missing & masks['non_c_invoice']).sum())}, negative quantity {_integer((customer_missing & quantities['negative']).sum())}, zero price {_integer((customer_missing & zero_price).sum())}.",
        ],
        "- Customer ID representation and missingness need an explicit Phase 2 decision; missing IDs are not rejected here.",
    )
    lines += _section(
        "Product / StockCode Findings",
        [
            f"- Distinct non-null StockCodes: {_integer(masks['stockcode'].nunique(dropna=True))}.",
            f"- Null StockCode rows: {_integer(masks['stockcode_null'].sum())}; blank-string rows: {_integer(masks['stockcode_blank'].sum())}.",
            stock_table,
            f"- Missing Description rows: {_integer(missing_description.sum())} ({_percent(percentage(int(missing_description.sum()), combined_rows))}).",
            f"- Missing-description overlap: missing Customer ID {_integer((missing_description & customer_missing).sum())}; C-prefixed invoice {_integer((missing_description & masks['c_invoice']).sum())}.",
            f"- Missing-description quantity relationship: negative {_integer((missing_description & quantities['negative']).sum())}, zero {_integer((missing_description & quantities['zero']).sum())}, positive {_integer((missing_description & quantities['positive']).sum())}, null/blank/unparseable {_integer((missing_description & quantities['null_blank_or_invalid']).sum())}.",
            f"- Missing-description price relationship: negative {_integer((missing_description & prices.lt(0)).sum())}, zero {_integer((missing_description & prices.eq(0)).sum())}, positive {_integer((missing_description & prices.gt(0)).sum())}, null/blank/unparseable {_integer((missing_description & prices.isna()).sum())}.",
            f"- Frequent StockCodes on missing-description rows: `{as_json(top_values(masks['stockcode'].loc[missing_description]))}`.",
            f"- StockCodes with one description: {_integer(descriptions['one_description'])}; multiple descriptions: {_integer(descriptions['multiple_descriptions'])}; maximum for one StockCode: {_integer(descriptions['maximum_descriptions'])}.",
            description_table,
        ],
        "- Non-standard StockCodes and multiple descriptions can be legitimate; syntax alone does not define a cleaning rule.",
    )
    lines += _section(
        "Country Findings",
        [
            f"- Distinct non-null country values: {_integer(masks['country'].nunique(dropna=True))}.",
            f"- Null rows: {_integer(masks['country_null'].sum())}; blank-string rows: {_integer(masks['country_blank'].sum())}.",
            f"- Rows with outer whitespace: {_integer(country_summary.loc[country_summary['has_outer_whitespace'], 'row_count'].sum())}.",
            f"- Comparison-key groups with multiple raw spellings/cases: {_integer(len(analysis['country_variants']))}; candidates: `{as_json(analysis['country_variants'])}`.",
            country_table,
        ],
        "- Comparison keys are profiling aids only and do not establish a normalization rule.",
    )
    lines += _section(
        "Duplicate-Looking Rows",
        [
            f"- Participating rows: {_integer(duplicates['participating_rows'])}; groups: {_integer(duplicates['group_count'])}; largest group: {_integer(duplicates['largest_group_size'])} rows.",
            duplicate_table,
            f"> {DUPLICATE_DISCLAIMER}",
        ],
        "- Repeated business-field values can be separate source-line occurrences, so physical source-row metadata must remain available.",
    )
    lines += _section(
        "Important Anomalies",
        [
            f"- C-prefixed rows with positive quantity: {_integer((masks['c_invoice'] & quantities['positive']).sum())}.",
            f"- Non-C rows with negative quantity: {_integer((masks['non_c_invoice'] & quantities['negative']).sum())}.",
            f"- Negative-price rows: {_integer(negative_price.sum())}; zero-price rows: {_integer(zero_price.sum())}.",
            f"- Unexpected invoice-format rows: {_integer(masks['unexpected_invoice'].sum())}.",
            f"- Missing-customer rows: {_integer(customer_missing.sum())}; missing-description rows: {_integer(missing_description.sum())}.",
        ],
        "- Each anomaly category may contain several business situations; Phase 2 should review evidence before assigning treatment.",
    )
    lines.extend(
        [
            "## Open Questions for Phase 2",
            "",
            "- How should C-prefixed invoices with non-negative quantities be treated?",
            "- How should negative quantities on non-C invoices be classified?",
            "- Which zero-price or negative-price patterns are valid adjustments or unusable records?",
            "- Which critical nulls require rejection, and which should remain with an UNKNOWN member or quality flag?",
            "- How should non-standard StockCodes be represented without assuming they are invalid products?",
            "- Which evidence-based rule should select a description when one StockCode has multiple descriptions?",
            "- Do any country labels need normalization after source semantics are reviewed?",
            "- Which suspicious but usable rows need a small documented quality-flag set?",
            "",
        ]
    )
    return "\n".join(lines)

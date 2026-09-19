"""Core masks and aggregate metric tables used by the Phase 1 profiler."""

from __future__ import annotations

from typing import Any

import pandas as pd

from .common import blank_mask, derived_numeric, format_date, percentage


def build_masks(frame: pd.DataFrame) -> dict[str, Any]:
    invoice = frame["Invoice"]
    invoice_text = invoice.astype("string")
    invoice_blank = blank_mask(invoice)
    invoice_nonblank = invoice.notna() & ~invoice_blank
    c_invoice = (invoice_nonblank & invoice_text.str.startswith("C", na=False)).fillna(False)
    non_c_invoice = (invoice_nonblank & ~c_invoice).fillna(False)
    standard_invoice = (
        invoice_text.str.fullmatch(r"\d+", na=False)
        | invoice_text.str.fullmatch(r"C\d+", na=False)
    )

    quantity, quantity_blank, quantity_invalid = derived_numeric(frame["Quantity"])
    price, price_blank, price_invalid = derived_numeric(frame["Price"])
    quantity_masks = {
        "negative": quantity.lt(0).fillna(False),
        "zero": quantity.eq(0).fillna(False),
        "positive": quantity.gt(0).fillna(False),
        "null_blank_or_invalid": quantity.isna(),
    }

    customer = frame["Customer ID"]
    customer_numeric, customer_blank, customer_invalid = derived_numeric(customer)
    customer_fractional = (
        customer_numeric.notna()
        & (customer_numeric - customer_numeric.round()).abs().gt(1e-9)
    )

    invoice_dates = frame["InvoiceDate"]
    date_blank = blank_mask(invoice_dates)
    parsed_dates = pd.to_datetime(invoice_dates, errors="coerce")

    return {
        "invoice": invoice,
        "invoice_text": invoice_text,
        "invoice_null": invoice.isna(),
        "invoice_blank": invoice_blank,
        "invoice_nonblank": invoice_nonblank,
        "c_invoice": c_invoice,
        "non_c_invoice": non_c_invoice,
        "unexpected_invoice": (invoice_nonblank & ~standard_invoice).fillna(False),
        "quantity": quantity,
        "quantity_blank": quantity_blank,
        "quantity_invalid": quantity_invalid,
        "quantity_masks": quantity_masks,
        "price": price,
        "price_blank": price_blank,
        "price_invalid": price_invalid,
        "negative_price": price.lt(0).fillna(False),
        "zero_price": price.eq(0).fillna(False),
        "customer": customer,
        "customer_missing": customer.isna(),
        "customer_blank": customer_blank,
        "customer_invalid": customer_invalid,
        "customer_fractional": customer_fractional,
        "customer_dot_zero": customer.astype("string").str.endswith(".0", na=False),
        "description": frame["Description"],
        "description_missing": frame["Description"].isna(),
        "description_blank": blank_mask(frame["Description"]),
        "country": frame["Country"],
        "country_null": frame["Country"].isna(),
        "country_blank": blank_mask(frame["Country"]),
        "stockcode": frame["StockCode"],
        "stockcode_null": frame["StockCode"].isna(),
        "stockcode_blank": blank_mask(frame["StockCode"]),
        "parsed_dates": parsed_dates,
        "date_null": invoice_dates.isna(),
        "date_blank": date_blank,
        "date_invalid": (invoice_dates.notna() & ~date_blank & parsed_dates.isna()).fillna(False),
    }


def profile_dates(masks: dict[str, Any], sheet_summary: pd.DataFrame) -> dict[str, Any]:
    valid_dates = masks["parsed_dates"].dropna()
    by_year = valid_dates.dt.year.value_counts().sort_index()
    by_month = valid_dates.dt.to_period("M").astype(str).value_counts().sort_index()

    sheet_mins = pd.to_datetime(sheet_summary["min_invoice_datetime"], errors="coerce")
    sheet_maxes = pd.to_datetime(sheet_summary["max_invoice_datetime"], errors="coerce")
    overlap_start = sheet_mins.max() if sheet_mins.notna().all() else pd.NaT
    overlap_end = sheet_maxes.min() if sheet_maxes.notna().all() else pd.NaT
    if pd.notna(overlap_start) and pd.notna(overlap_end) and overlap_start > overlap_end:
        overlap_start = overlap_end = pd.NaT

    return {
        "minimum": format_date(valid_dates.min()),
        "maximum": format_date(valid_dates.max()),
        "null_count": int(masks["date_null"].sum()),
        "blank_count": int(masks["date_blank"].sum()),
        "invalid_count": int(masks["date_invalid"].sum()),
        "overlap_start": format_date(overlap_start),
        "overlap_end": format_date(overlap_end),
        "rows_by_year": [(str(value), int(count)) for value, count in by_year.items()],
        "rows_by_month": [(str(value), int(count)) for value, count in by_month.items()],
    }


def profile_cardinality(frame: pd.DataFrame, masks: dict[str, Any]) -> pd.DataFrame:
    definitions = [
        ("unique_invoices_non_null", "Invoice", None),
        ("unique_invoices_non_null_non_blank", "Invoice", masks["invoice_blank"]),
        ("unique_stockcodes_non_null", "StockCode", None),
        ("unique_stockcodes_non_null_non_blank", "StockCode", masks["stockcode_blank"]),
        ("unique_known_customer_ids", "Customer ID", None),
        ("unique_known_customer_ids_non_blank", "Customer ID", masks["customer_blank"]),
        ("unique_countries_non_null", "Country", None),
        ("unique_countries_non_null_non_blank", "Country", masks["country_blank"]),
    ]
    records = []
    for metric, column, excluded_mask in definitions:
        values = frame[column] if excluded_mask is None else frame.loc[~excluded_mask, column]
        records.append(
            {
                "scope": "combined",
                "metric": metric,
                "value": int(values.nunique(dropna=True)),
                "definition": f"Distinct raw {column} values excluding true nulls"
                + (" and blank strings." if excluded_mask is not None else "."),
            }
        )
    return pd.DataFrame(records)


def profile_cancellations(
    masks: dict[str, Any],
    combined_rows: int,
) -> pd.DataFrame:
    invoice_masks = {
        "c_prefixed": masks["c_invoice"],
        "non_c": masks["non_c_invoice"],
        "invoice_null": masks["invoice_null"],
        "invoice_blank": masks["invoice_blank"],
    }
    records = []
    for invoice_class, invoice_mask in invoice_masks.items():
        for quantity_class, quantity_mask in masks["quantity_masks"].items():
            selected = invoice_mask & quantity_mask
            records.append(
                {
                    "invoice_class": invoice_class,
                    "quantity_class": quantity_class,
                    "row_count": int(selected.sum()),
                    "row_percentage": percentage(int(selected.sum()), combined_rows),
                    "distinct_invoice_count": int(
                        masks["invoice"].loc[selected].nunique(dropna=True)
                    ),
                }
            )
    result = pd.DataFrame(records)
    if int(result["row_count"].sum()) != combined_rows:
        raise AssertionError("Invoice/quantity profiling matrix does not reconcile.")
    return result


def profile_countries(
    masks: dict[str, Any],
    combined_rows: int,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    token = "__PROFILE_NULL_COUNTRY__"
    country_text = masks["country"].astype("string")
    if country_text.eq(token).any():
        raise ValueError("The reserved country profiling token occurs in source data.")

    country_key = country_text.fillna(token)
    working = pd.DataFrame(
        {
            "country_key": country_key,
            "Invoice": masks["invoice"],
            "Customer ID": masks["customer"],
        }
    )
    summary = (
        working.groupby("country_key", sort=False, dropna=False)
        .agg(
            row_count=("country_key", "size"),
            distinct_invoices=("Invoice", lambda values: values.nunique(dropna=True)),
            known_customers=("Customer ID", lambda values: values.nunique(dropna=True)),
        )
        .reset_index()
    )
    missing_by_country = (
        working.loc[masks["customer_missing"]]
        .groupby("country_key", sort=False, dropna=False)
        .size()
    )
    summary["missing_customer_rows"] = (
        summary["country_key"].map(missing_by_country).fillna(0).astype(int)
    )
    summary["row_percentage"] = summary["row_count"] / combined_rows * 100.0
    summary["missing_customer_percentage_within_country"] = (
        summary["missing_customer_rows"] / summary["row_count"] * 100.0
    )
    summary["value_status"] = summary["country_key"].map(
        lambda value: "null" if value == token else ("blank" if str(value).strip() == "" else "value")
    )
    summary["country_value"] = summary["country_key"].map(
        lambda value: None if value == token else value
    )
    summary["has_outer_whitespace"] = summary.apply(
        lambda row: row["value_status"] == "value"
        and str(row["country_value"]) != str(row["country_value"]).strip(),
        axis=1,
    )
    summary["comparison_key_for_profiling_only"] = summary.apply(
        lambda row: ""
        if row["value_status"] != "value"
        else str(row["country_value"]).strip().casefold(),
        axis=1,
    )
    summary = summary[
        [
            "country_value",
            "value_status",
            "row_count",
            "row_percentage",
            "distinct_invoices",
            "known_customers",
            "missing_customer_rows",
            "missing_customer_percentage_within_country",
            "has_outer_whitespace",
            "comparison_key_for_profiling_only",
        ]
    ].copy()
    summary["_sort_value"] = summary["country_value"].astype("string")
    summary = summary.sort_values(
        ["row_count", "_sort_value"], ascending=[False, True], kind="stable"
    ).drop(columns="_sort_value").reset_index(drop=True)
    if int(summary["row_count"].sum()) != combined_rows:
        raise AssertionError("Country row counts do not reconcile to the combined row count.")

    values = summary.loc[summary["value_status"].eq("value")]
    key_counts = values.groupby("comparison_key_for_profiling_only")["country_value"].nunique()
    variant_keys = sorted(key_counts.loc[key_counts.gt(1)].index)
    variants = [
        {
            "comparison_key": key,
            "raw_values": values.loc[
                values["comparison_key_for_profiling_only"].eq(key), "country_value"
            ].tolist(),
        }
        for key in variant_keys
    ]
    return summary, variants

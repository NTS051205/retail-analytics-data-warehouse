"""Read and reconcile every worksheet without changing the source workbook."""

from __future__ import annotations

from typing import Any

import pandas as pd

from ..config import RAW_DATA_PATH
from .common import EXPECTED_COLUMNS, as_json, blank_mask, format_date, null_records, safe_scalar


def load_workbook() -> dict[str, Any]:
    """Read each worksheet once, validate its schema, then combine it in memory."""
    frames: list[pd.DataFrame] = []
    sheet_records: list[dict[str, Any]] = []
    missing_records: list[dict[str, Any]] = []
    read_counts: dict[str, int] = {}
    reference_columns: list[Any] | None = None

    with pd.ExcelFile(RAW_DATA_PATH, engine="openpyxl") as workbook:
        sheet_names = list(workbook.sheet_names)
        if not sheet_names:
            raise ValueError("The source workbook contains no worksheets.")

        for sheet_name in sheet_names:
            print(f"[read] Loading worksheet: {sheet_name}", flush=True)
            frame = workbook.parse(sheet_name=sheet_name)
            read_counts[sheet_name] = read_counts.get(sheet_name, 0) + 1
            columns = list(frame.columns)
            reference_columns = reference_columns or columns.copy()

            date_source = frame.get(
                "InvoiceDate",
                pd.Series(pd.NA, index=frame.index, dtype="object"),
            )
            parsed_dates = pd.to_datetime(date_source, errors="coerce")
            invalid_dates = date_source.notna() & ~blank_mask(date_source) & parsed_dates.isna()

            sheet_records.append(
                {
                    "sheet_name": sheet_name,
                    "row_count": len(frame),
                    "column_count": len(columns),
                    "column_names_json": as_json([safe_scalar(value) for value in columns]),
                    "pandas_dtypes_json": as_json(
                        {str(column): str(frame[column].dtype) for column in columns}
                    ),
                    "min_invoice_datetime": format_date(parsed_dates.min()),
                    "max_invoice_datetime": format_date(parsed_dates.max()),
                    "invalid_invoice_datetime_count": int(invalid_dates.sum()),
                    "schema_matches_reference": columns == reference_columns,
                    "missing_expected_columns_json": as_json(
                        [column for column in EXPECTED_COLUMNS if column not in columns]
                    ),
                    "extra_columns_json": as_json(
                        [safe_scalar(column) for column in columns if column not in EXPECTED_COLUMNS]
                    ),
                    "headers_with_outer_whitespace_json": as_json(
                        [str(column) for column in columns if str(column) != str(column).strip()]
                    ),
                }
            )
            missing_records.extend(null_records(frame, columns, "sheet", sheet_name))

            reserved = {"source_sheet", "source_row_number"}.intersection(columns)
            if reserved:
                raise ValueError(f"Source workbook contains reserved columns: {sorted(reserved)}")
            frame.insert(0, "source_row_number", range(2, len(frame) + 2))
            frame.insert(0, "source_sheet", sheet_name)
            frames.append(frame)

    assert reference_columns is not None
    sheet_summary = pd.DataFrame(sheet_records)
    if not bool(sheet_summary["schema_matches_reference"].all()):
        raise ValueError("Workbook sheet schemas differ; combined profiling was not performed.")

    missing_columns = [column for column in EXPECTED_COLUMNS if column not in reference_columns]
    if missing_columns:
        raise ValueError("Required source columns are missing: " + ", ".join(missing_columns))

    print("[read] Combining verified worksheet frames in memory", flush=True)
    combined = pd.concat(frames, ignore_index=True, copy=False)
    expected_rows = int(sheet_summary["row_count"].sum())
    if expected_rows != len(combined):
        raise AssertionError(
            f"Sheet row total {expected_rows} does not match combined rows {len(combined)}."
        )
    if any(count != 1 for count in read_counts.values()):
        raise AssertionError("At least one worksheet was read more than once.")

    missing_records.extend(null_records(combined, reference_columns, "combined", ""))
    return {
        "frame": combined,
        "sheet_names": sheet_names,
        "sheet_summary": sheet_summary,
        "null_summary": pd.DataFrame(missing_records),
        "source_columns": reference_columns,
        "read_counts": read_counts,
    }

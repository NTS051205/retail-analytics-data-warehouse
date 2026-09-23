"""Apply the approved Phase 2 contract and write Phase 3 local artifacts."""

from __future__ import annotations

import json
import math
import re
import shutil
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from numbers import Integral, Real
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq
from pandas.api.types import is_datetime64_any_dtype, is_float_dtype, is_integer_dtype

from .benchmark import run_csv_parquet_benchmark
from .config import (
    PHASE3_SUMMARY_PATH,
    RAW_DATA_PATH,
    REJECTED_DATA_DIR,
    REJECTED_TRANSACTIONS_PATH,
    SOURCE_WORKBOOK_SHA256,
    TRANSACTIONS_PARQUET_DIR,
)
from .extract import (
    LINEAGE_COLUMNS,
    assert_source_unchanged,
    build_source_row_ids,
    extract_workbook,
)
from .profiling.common import EXPECTED_COLUMNS, blank_mask
from .quality import (
    ACCEPT,
    ACCEPTED_CLASSIFICATIONS,
    ACCEPT_WITH_QUALITY_FLAG,
    QUALITY_FLAG_ORDER,
    REJECT,
    REJECT_REASON_ORDER,
    classify_rows,
    core_cancellation_line_mask,
    core_commercial_sale_mask,
    encode_ordered_codes,
)


SQL_INT_MIN = -2_147_483_648
SQL_INT_MAX = 2_147_483_647
PRICE_QUANTUM = Decimal("0.0001")
PRICE_MAX_ABS = Decimal("999999999999999.9999")
NUMERIC_TEXT_PATTERN = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$"
)

SOURCE_TO_NORMALIZED = {
    "Invoice": "invoice_no",
    "StockCode": "stock_code",
    "Description": "description",
    "Quantity": "quantity",
    "InvoiceDate": "invoice_datetime",
    "Price": "unit_price",
    "Customer ID": "customer_id",
    "Country": "country",
}

NORMALIZED_COLUMNS = list(SOURCE_TO_NORMALIZED.values())
DERIVED_COLUMNS = ["line_amount", "is_cancelled", "batch_month", "loaded_at"]
ACCEPTED_OUTPUT_COLUMNS = [
    *LINEAGE_COLUMNS,
    *NORMALIZED_COLUMNS,
    *DERIVED_COLUMNS,
    "classification",
    "quality_flags",
    *QUALITY_FLAG_ORDER,
]

ACCEPTED_ARROW_SCHEMA = pa.schema(
    [
        pa.field("source_sheet", pa.string(), nullable=False),
        pa.field("source_row_number", pa.int64(), nullable=False),
        pa.field("source_row_id", pa.string(), nullable=False),
        pa.field("invoice_no", pa.string(), nullable=False),
        pa.field("stock_code", pa.string(), nullable=False),
        pa.field("description", pa.string()),
        pa.field("quantity", pa.int32(), nullable=False),
        # Parquet represents timestamp logical types at ms/us/ns resolution.
        # Values are still normalized to whole seconds before this write step.
        pa.field("invoice_datetime", pa.timestamp("ms"), nullable=False),
        pa.field("unit_price", pa.decimal128(19, 4), nullable=False),
        pa.field("customer_id", pa.string()),
        pa.field("country", pa.string()),
        pa.field("line_amount", pa.decimal128(30, 4), nullable=False),
        pa.field("is_cancelled", pa.bool_(), nullable=False),
        pa.field("batch_month", pa.date32(), nullable=False),
        pa.field("loaded_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("classification", pa.string(), nullable=False),
        pa.field("quality_flags", pa.string(), nullable=False),
        *[
            pa.field(flag, pa.bool_(), nullable=False)
            for flag in QUALITY_FLAG_ORDER
        ],
    ]
)

PARTITION_SCHEMA = pa.schema(
    [
        pa.field("year", pa.string(), nullable=False),
        pa.field("month", pa.string(), nullable=False),
    ]
)


@dataclass
class TransformationResult:
    accepted: pd.DataFrame
    rejected: pd.DataFrame
    source_count: int
    classification_counts: dict[str, int]
    reject_reason_counts: dict[str, int]
    quality_flag_counts: dict[str, int]
    core_commercial_line_count: int
    core_cancellation_line_count: int

    @property
    def accepted_count(self) -> int:
        return len(self.accepted)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)

    @property
    def accepted_with_flags_count(self) -> int:
        return self.classification_counts.get(ACCEPT_WITH_QUALITY_FLAG, 0)


def _is_missing_scalar(value: Any) -> bool:
    if value is None or value is pd.NA:
        return True
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        return False
    try:
        return bool(missing)
    except (TypeError, ValueError):
        return False


def _source_missing_mask(series: pd.Series) -> pd.Series:
    return (series.isna() | blank_mask(series)).fillna(False)


def _normalize_identifier(value: Any, *, uppercase: bool) -> str | None:
    if _is_missing_scalar(value):
        return None

    text: str
    if isinstance(value, bool):
        text = str(value)
    elif isinstance(value, Integral):
        text = str(int(value))
    elif isinstance(value, Decimal):
        if value.is_finite() and value == value.to_integral_value():
            text = str(int(value))
        else:
            text = str(value)
    elif isinstance(value, Real):
        numeric = float(value)
        if math.isfinite(numeric) and numeric.is_integer():
            text = str(int(numeric))
        else:
            text = str(value)
    else:
        text = str(value)

    text = text.strip()
    if not text:
        return None
    return text.upper() if uppercase else text


def _normalize_optional_text(series: pd.Series) -> pd.Series:
    normalized = series.astype("string").str.strip()
    return normalized.mask(normalized.eq(""), pd.NA)


def _parse_integral_decimal(value: Any) -> int | None:
    if _is_missing_scalar(value) or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text or not NUMERIC_TEXT_PATTERN.fullmatch(text):
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if not number.is_finite() or number != number.to_integral_value():
        return None
    return int(number)


def _normalize_customer_id(value: Any) -> str | None:
    parsed = _parse_integral_decimal(value)
    return None if parsed is None else str(parsed)


def _normalize_quantity(
    source: pd.Series,
    missing: pd.Series,
) -> tuple[pd.Series, pd.Series]:
    if is_integer_dtype(source.dtype):
        numeric = source.astype("Int64")
        valid = numeric.between(SQL_INT_MIN, SQL_INT_MAX).fillna(False) & ~missing
        quantity = numeric.where(valid).astype("Int32")
    elif is_float_dtype(source.dtype):
        numeric = pd.to_numeric(source.mask(missing), errors="coerce")
        finite = numeric.notna() & ~numeric.isin([float("inf"), float("-inf")])
        integral = numeric.mod(1).eq(0).fillna(False)
        in_range = numeric.between(SQL_INT_MIN, SQL_INT_MAX).fillna(False)
        valid = finite & integral & in_range & ~missing
        quantity = numeric.where(valid).astype("Int32")
    else:
        parsed = source.map(_parse_integral_decimal)
        numeric = pd.to_numeric(parsed, errors="coerce")
        valid = numeric.between(SQL_INT_MIN, SQL_INT_MAX).fillna(False) & ~missing
        quantity = numeric.where(valid).astype("Int32")

    invalid = (~missing & quantity.isna()).fillna(False)
    return quantity, invalid


def _parse_wall_clock(value: Any) -> pd.Timestamp | pd.NaT:
    if _is_missing_scalar(value):
        return pd.NaT
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError):
        return pd.NaT
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_localize(None)
    return timestamp.floor("s")


def _normalize_invoice_datetime(
    source: pd.Series,
    missing: pd.Series,
) -> tuple[pd.Series, pd.Series]:
    try:
        parsed = pd.to_datetime(source.mask(missing), errors="coerce")
        if not is_datetime64_any_dtype(parsed.dtype):
            raise TypeError("Mixed datetime representation requires scalar parsing.")
        if parsed.dt.tz is not None:
            parsed = parsed.dt.tz_localize(None)
        parsed = parsed.dt.floor("s")
    except (AttributeError, TypeError, ValueError):
        parsed = source.map(_parse_wall_clock)
        parsed = pd.to_datetime(parsed, errors="coerce").dt.floor("s")
    invalid = parsed.isna().fillna(True)
    return parsed, invalid


def _parse_price(value: Any) -> Decimal | None:
    if _is_missing_scalar(value) or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text or not NUMERIC_TEXT_PATTERN.fullmatch(text):
        return None
    try:
        value_decimal = Decimal(text)
        if not value_decimal.is_finite():
            return None
        with localcontext() as context:
            context.prec = 40
            normalized = value_decimal.quantize(PRICE_QUANTUM)
    except (InvalidOperation, ValueError):
        return None
    if normalized != value_decimal or abs(normalized) > PRICE_MAX_ABS:
        return None
    return normalized


def _normalize_loaded_at(value: Any | None) -> pd.Timestamp:
    timestamp = pd.Timestamp.now(tz="UTC") if value is None else pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.floor("us")


def _validated_lineage(source: pd.DataFrame) -> pd.DataFrame:
    source_sheet = source["source_sheet"].astype("string")
    invalid_sheet = source_sheet.isna() | source_sheet.str.strip().eq("")
    if invalid_sheet.any():
        raise ValueError("Every source row must have a non-empty source_sheet.")

    row_number_numeric = pd.to_numeric(source["source_row_number"], errors="coerce")
    valid_row_number = (
        row_number_numeric.notna()
        & row_number_numeric.mod(1).eq(0)
        & row_number_numeric.ge(2)
    )
    if not valid_row_number.all():
        raise ValueError("Every source row must have an integer source_row_number >= 2.")
    source_row_number = row_number_numeric.astype("int64")

    expected_ids = build_source_row_ids(
        source_sheet,
        source_row_number,
        index=source.index,
    )
    if "source_row_id" in source.columns:
        supplied = source["source_row_id"].astype("string")
        if not supplied.equals(expected_ids):
            raise ValueError("A supplied source_row_id does not match physical source identity.")
    if expected_ids.duplicated().any():
        raise AssertionError(
            "Duplicate physical source identity/source_row_id detected; the batch cannot continue."
        )

    return pd.DataFrame(
        {
            "source_sheet": source_sheet,
            "source_row_number": source_row_number,
            "source_row_id": expected_ids,
        },
        index=source.index,
    )


def transform_source(
    source_frame: pd.DataFrame,
    *,
    loaded_at: Any | None = None,
) -> TransformationResult:
    """Transform one extracted batch without deduplicating source occurrences."""
    required = ["source_sheet", "source_row_number", *EXPECTED_COLUMNS]
    missing_columns = [column for column in required if column not in source_frame.columns]
    if missing_columns:
        raise ValueError(f"Transformation input is missing columns: {missing_columns}")

    source = source_frame.reset_index(drop=True)
    lineage = _validated_lineage(source)
    batch_loaded_at = _normalize_loaded_at(loaded_at)
    normalized = lineage.copy()

    normalized["invoice_no"] = source["Invoice"].map(
        lambda value: _normalize_identifier(value, uppercase=True)
    ).astype("string")
    normalized["stock_code"] = source["StockCode"].map(
        lambda value: _normalize_identifier(value, uppercase=False)
    ).astype("string")
    normalized["description"] = _normalize_optional_text(source["Description"])

    quantity_missing = _source_missing_mask(source["Quantity"])
    quantity, quantity_invalid = _normalize_quantity(
        source["Quantity"], quantity_missing
    )
    normalized["quantity"] = quantity

    date_missing = _source_missing_mask(source["InvoiceDate"])
    invoice_datetime, date_invalid = _normalize_invoice_datetime(
        source["InvoiceDate"], date_missing
    )
    normalized["invoice_datetime"] = invoice_datetime

    price_missing = _source_missing_mask(source["Price"])
    unit_price = source["Price"].map(_parse_price).astype("object")
    price_invalid = (~price_missing & unit_price.isna()).fillna(False)
    normalized["unit_price"] = unit_price

    customer_missing = _source_missing_mask(source["Customer ID"])
    customer_id = source["Customer ID"].map(_normalize_customer_id).astype("string")
    customer_invalid = (~customer_missing & customer_id.isna()).fillna(False)
    normalized["customer_id"] = customer_id
    normalized["country"] = _normalize_optional_text(source["Country"])

    normalized["is_cancelled"] = normalized["invoice_no"].str.startswith(
        "C", na=False
    ).astype(bool)

    valid_amount = normalized["quantity"].notna() & normalized["unit_price"].notna()
    line_amount = pd.Series(None, index=source.index, dtype="object")
    with localcontext() as context:
        context.prec = 40
        line_amount.loc[valid_amount] = (
            normalized.loc[valid_amount, "quantity"].astype("int64").astype("object")
            * normalized.loc[valid_amount, "unit_price"]
        )
    normalized["line_amount"] = line_amount
    normalized["batch_month"] = (
        normalized["invoice_datetime"].dt.to_period("M").dt.to_timestamp().dt.date
    )
    normalized["loaded_at"] = batch_loaded_at

    invoice_present = normalized["invoice_no"].notna()
    standard_invoice = normalized["invoice_no"].str.fullmatch(
        r"[0-9]+|C[0-9]+", na=False
    )
    quantity_valid = normalized["quantity"].notna()
    negative_price = normalized["unit_price"].map(
        lambda value: isinstance(value, Decimal) and value < 0
    )
    zero_price = normalized["unit_price"].map(
        lambda value: isinstance(value, Decimal) and value == 0
    )
    quantity_sign_mismatch = (
        invoice_present
        & quantity_valid
        & (
            (
                normalized["is_cancelled"]
                & normalized["quantity"].ge(0).fillna(False)
            )
            | (
                ~normalized["is_cancelled"]
                & normalized["quantity"].le(0).fillna(False)
            )
        )
    ).fillna(False)

    quality_masks = {
        "MISSING_CUSTOMER": customer_missing,
        "INVALID_CUSTOMER_ID": customer_invalid,
        "MISSING_DESCRIPTION": normalized["description"].isna(),
        "ZERO_PRICE": zero_price,
        "QUANTITY_SIGN_MISMATCH": quantity_sign_mismatch,
        "NONSTANDARD_INVOICE": (invoice_present & ~standard_invoice).fillna(False),
        "MISSING_COUNTRY": normalized["country"].isna(),
    }
    for flag in QUALITY_FLAG_ORDER:
        normalized[flag] = quality_masks[flag].fillna(False).astype(bool)
    normalized["quality_flags"] = encode_ordered_codes(
        quality_masks,
        QUALITY_FLAG_ORDER,
        index=source.index,
    )

    reject_masks = {
        "MISSING_INVOICE": normalized["invoice_no"].isna(),
        "MISSING_STOCK_CODE": normalized["stock_code"].isna(),
        "INVALID_INVOICE_DATE": date_invalid,
        "MISSING_QUANTITY": quantity_missing,
        "INVALID_QUANTITY": quantity_invalid,
        "MISSING_PRICE": price_missing,
        "INVALID_PRICE": price_invalid,
        "NEGATIVE_PRICE_ADJUSTMENT": negative_price,
    }
    reject_reason = encode_ordered_codes(
        reject_masks,
        REJECT_REASON_ORDER,
        index=source.index,
    )
    normalized["classification"] = classify_rows(
        reject_reason,
        normalized["quality_flags"],
    )

    reject_selected = normalized["classification"].eq(REJECT)
    accepted = normalized.loc[~reject_selected, ACCEPTED_OUTPUT_COLUMNS].copy()

    rejected = source.loc[
        reject_selected,
        ["source_sheet", "source_row_number", *EXPECTED_COLUMNS],
    ].copy()
    rejected.insert(
        2,
        "source_row_id",
        normalized.loc[reject_selected, "source_row_id"],
    )
    for column in [
        *NORMALIZED_COLUMNS,
        *DERIVED_COLUMNS,
        "classification",
        "quality_flags",
        *QUALITY_FLAG_ORDER,
    ]:
        rejected[column] = normalized.loc[reject_selected, column]
    rejected["reject_reason"] = reject_reason.loc[reject_selected]
    rejected["rejected_at"] = batch_loaded_at

    classification_counts = {
        classification: int(normalized["classification"].eq(classification).sum())
        for classification in (ACCEPT, ACCEPT_WITH_QUALITY_FLAG, REJECT)
    }
    reject_reason_counts = {
        reason: int(reject_masks[reason].sum()) for reason in REJECT_REASON_ORDER
    }
    accepted_mask = ~reject_selected
    quality_flag_counts = {
        flag: int((quality_masks[flag] & accepted_mask).sum())
        for flag in QUALITY_FLAG_ORDER
    }

    result = TransformationResult(
        accepted=accepted,
        rejected=rejected,
        source_count=len(source),
        classification_counts=classification_counts,
        reject_reason_counts=reject_reason_counts,
        quality_flag_counts=quality_flag_counts,
        core_commercial_line_count=int(core_commercial_sale_mask(accepted).sum()),
        core_cancellation_line_count=int(core_cancellation_line_mask(accepted).sum()),
    )
    validate_transformation_result(result)
    return result


def validate_transformation_result(result: TransformationResult) -> None:
    """Enforce Phase 3 classification, traceability, and reconciliation rules."""
    if result.accepted_count + result.rejected_count != result.source_count:
        raise AssertionError("Accepted plus rejected rows do not equal source rows.")

    accepted_by_class = (
        result.classification_counts.get(ACCEPT, 0)
        + result.classification_counts.get(ACCEPT_WITH_QUALITY_FLAG, 0)
    )
    if accepted_by_class != result.accepted_count:
        raise AssertionError("Accepted classifications do not reconcile.")
    if result.classification_counts.get(REJECT, 0) != result.rejected_count:
        raise AssertionError("Rejected classifications do not reconcile.")
    if not result.accepted["classification"].isin(ACCEPTED_CLASSIFICATIONS).all():
        raise AssertionError("Rejected classification appeared in accepted output.")
    if not result.rejected["classification"].eq(REJECT).all():
        raise AssertionError("A rejected output row does not have REJECT classification.")
    if result.rejected["reject_reason"].fillna("").eq("").any():
        raise AssertionError("Every rejected row must contain an approved reason code.")

    all_ids = pd.concat(
        [result.accepted["source_row_id"], result.rejected["source_row_id"]],
        ignore_index=True,
    )
    if all_ids.isna().any() or all_ids.duplicated().any():
        raise AssertionError("source_row_id must be populated and unique across the batch.")


def _reset_generated_directory(path: Path, *, allowed_parent: Path) -> None:
    resolved = path.resolve()
    parent = allowed_parent.resolve()
    if resolved == parent or parent not in resolved.parents:
        raise ValueError(f"Refusing to replace unsafe generated-data path: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True, exist_ok=True)


def write_rejected_csv(
    rejected: pd.DataFrame,
    path: Path = REJECTED_TRANSACTIONS_PATH,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rejected.to_csv(path, index=False, encoding="utf-8", lineterminator="\n")
    return path


def write_accepted_parquet(
    accepted: pd.DataFrame,
    output_dir: Path = TRANSACTIONS_PARQUET_DIR,
) -> dict[str, object]:
    """Write accepted rows with one stable schema and Hive year/month partitions."""
    if accepted.empty:
        raise ValueError("Cannot write an empty accepted Parquet dataset.")
    _reset_generated_directory(output_dir, allowed_parent=output_dir.parent)

    output = accepted.loc[:, ACCEPTED_OUTPUT_COLUMNS].copy()
    invoice_datetime = pd.to_datetime(output["invoice_datetime"], errors="raise")
    output["year"] = invoice_datetime.dt.strftime("%Y")
    output["month"] = invoice_datetime.dt.strftime("%m")
    arrow_schema = pa.schema([*ACCEPTED_ARROW_SCHEMA, *PARTITION_SCHEMA])
    table = pa.Table.from_pandas(
        output,
        schema=arrow_schema,
        preserve_index=False,
        safe=True,
    )
    partitioning = ds.partitioning(PARTITION_SCHEMA, flavor="hive")
    ds.write_dataset(
        table,
        base_dir=str(output_dir),
        format="parquet",
        partitioning=partitioning,
        basename_template="part-{i}.parquet",
        existing_data_behavior="error",
        max_rows_per_file=100_000,
        max_rows_per_group=100_000,
    )
    return validate_parquet_output(output_dir, accepted)


def validate_parquet_output(
    output_dir: Path,
    accepted: pd.DataFrame,
) -> dict[str, object]:
    parquet_files = sorted(output_dir.rglob("*.parquet"))
    if not parquet_files:
        raise AssertionError("No Parquet files were written.")

    expected_physical_schema = ACCEPTED_ARROW_SCHEMA.remove_metadata()
    for path in parquet_files:
        actual_schema = pq.read_schema(path).remove_metadata()
        expected_signature = [
            (field.name, field.type) for field in expected_physical_schema
        ]
        actual_signature = [(field.name, field.type) for field in actual_schema]
        if actual_signature != expected_signature:
            raise AssertionError(
                f"Parquet column/type schema mismatch in {path}. "
                f"Expected {expected_signature}; found {actual_signature}."
            )

    partitioning = ds.partitioning(PARTITION_SCHEMA, flavor="hive")
    dataset = ds.dataset(
        str(output_dir),
        format="parquet",
        partitioning=partitioning,
    )
    expected_logical_schema = pa.schema(
        [*ACCEPTED_ARROW_SCHEMA, *PARTITION_SCHEMA]
    ).remove_metadata()
    actual_logical_schema = dataset.schema.remove_metadata()
    expected_logical_signature = [
        (field.name, field.type) for field in expected_logical_schema
    ]
    actual_logical_signature = [
        (field.name, field.type) for field in actual_logical_schema
    ]
    if actual_logical_signature != expected_logical_signature:
        raise AssertionError(
            "Partitioned Parquet logical schema mismatch. "
            f"Expected {expected_logical_signature}; found {actual_logical_signature}."
        )

    row_count = dataset.count_rows()
    if row_count != len(accepted):
        raise AssertionError(
            f"Parquet rows ({row_count}) do not equal accepted rows ({len(accepted)})."
        )

    parquet_ids = dataset.to_table(columns=["source_row_id"])["source_row_id"].to_pandas()
    if parquet_ids.isna().any() or parquet_ids.duplicated().any():
        raise AssertionError("source_row_id is not unique in accepted Parquet output.")

    partition_paths: set[str] = set()
    for path in parquet_files:
        relative_parts = path.parent.relative_to(output_dir).parts
        if (
            len(relative_parts) != 2
            or re.fullmatch(r"year=[0-9]{4}", relative_parts[0]) is None
            or re.fullmatch(r"month=(0[1-9]|1[0-2])", relative_parts[1]) is None
        ):
            raise AssertionError(
                "Parquet partition path must use zero-padded "
                f"year=YYYY/month=MM: {path.parent}"
            )
        partition_paths.add("/".join(relative_parts))
    partitions = sorted(partition_paths)
    batch_months = pd.to_datetime(accepted["batch_month"], errors="raise")
    return {
        "output_dir": str(output_dir),
        "row_count": row_count,
        "file_count": len(parquet_files),
        "partition_count": len(partitions),
        "partitions": partitions,
        "minimum_batch_month": batch_months.min().date().isoformat(),
        "maximum_batch_month": batch_months.max().date().isoformat(),
        "source_row_id_unique": True,
        "representative_physical_schema": str(expected_physical_schema),
        "logical_dataset_schema": str(expected_logical_schema),
    }


def _write_summary(summary: dict[str, object], path: Path = PHASE3_SUMMARY_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def run_transform() -> dict[str, object]:
    """Run the complete Phase 3 local transformation and benchmark."""
    print(f"[start] Transforming {RAW_DATA_PATH}", flush=True)
    extracted = extract_workbook(
        RAW_DATA_PATH,
        expected_sha256=SOURCE_WORKBOOK_SHA256,
    )
    source_fingerprint = extracted.fingerprint
    source_sheet_names = extracted.sheet_names
    source_sheet_rows = extracted.row_counts

    print("[transform] Applying the approved Phase 2 contract", flush=True)
    result = transform_source(extracted.frame)
    del extracted

    print("[write] Writing rejected rows and accepted Parquet", flush=True)
    rejected_path = write_rejected_csv(result.rejected)
    parquet_summary = write_accepted_parquet(result.accepted)

    print("[benchmark] Measuring CSV and Parquet on a deterministic sample", flush=True)
    benchmark_summary = run_csv_parquet_benchmark(result.accepted)

    source_after = assert_source_unchanged(RAW_DATA_PATH, source_fingerprint)
    summary: dict[str, object] = {
        "source_workbook": str(RAW_DATA_PATH),
        "source_sha256_before": source_fingerprint.sha256,
        "source_sha256_after": source_after.sha256,
        "source_unchanged": True,
        "source_sheets": list(source_sheet_names),
        "source_rows_by_sheet": source_sheet_rows,
        "source_rows": result.source_count,
        "accepted_rows": result.accepted_count,
        "accepted_clean_rows": result.classification_counts.get(ACCEPT, 0),
        "accepted_with_quality_flags_rows": result.accepted_with_flags_count,
        "rejected_rows": result.rejected_count,
        "classification_counts": result.classification_counts,
        "reject_reason_counts": result.reject_reason_counts,
        "accepted_quality_flag_counts": result.quality_flag_counts,
        "core_commercial_line_count": result.core_commercial_line_count,
        "core_cancellation_line_count": result.core_cancellation_line_count,
        "rejected_output": str(rejected_path),
        "parquet": parquet_summary,
        "benchmark": benchmark_summary,
    }
    _write_summary(summary)

    print(
        f"[done] Source={result.source_count:,}; accepted={result.accepted_count:,}; "
        f"rejected={result.rejected_count:,}",
        flush=True,
    )
    print(f"[done] Phase 3 summary: {PHASE3_SUMMARY_PATH}", flush=True)
    return summary


def main() -> None:
    run_transform()


if __name__ == "__main__":
    main()

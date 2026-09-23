"""Read the immutable Online Retail II workbook with physical row lineage."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

from .config import RAW_DATA_PATH, SOURCE_WORKBOOK_SHA256
from .profiling.common import EXPECTED_COLUMNS, sha256


LINEAGE_COLUMNS = ["source_sheet", "source_row_number", "source_row_id"]


@dataclass(frozen=True)
class SourceFingerprint:
    """Filesystem and content identity used to prove source immutability."""

    size_bytes: int
    modified_time_ns: int
    sha256: str


@dataclass
class ExtractedWorkbook:
    """A single in-memory source batch and its extraction metadata."""

    frame: pd.DataFrame
    sheet_names: tuple[str, ...]
    row_counts: dict[str, int]
    fingerprint: SourceFingerprint


def make_source_row_id(source_sheet: str, source_row_number: int) -> str:
    """Return the Phase 2 lowercase SHA-256 physical-row identifier."""
    if not isinstance(source_sheet, str) or not source_sheet:
        raise ValueError("source_sheet must be a non-empty string.")
    if isinstance(source_row_number, bool):
        raise ValueError("source_row_number must be an integer Excel row number.")
    try:
        row_number = int(source_row_number)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("source_row_number must be an integer Excel row number.") from exc
    if row_number < 2 or row_number != source_row_number:
        raise ValueError("source_row_number must be an integer greater than or equal to 2.")

    payload = f"{source_sheet}|{row_number}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_source_row_ids(
    source_sheets: Iterable[object],
    source_row_numbers: Iterable[object],
    *,
    index: pd.Index | None = None,
) -> pd.Series:
    """Build deterministic IDs without using mutable business values."""
    values = [
        make_source_row_id(str(sheet), row_number)
        for sheet, row_number in zip(source_sheets, source_row_numbers, strict=True)
    ]
    return pd.Series(values, index=index, dtype="string", name="source_row_id")


def attach_source_lineage(frame: pd.DataFrame, sheet_name: str) -> pd.DataFrame:
    """Attach the documented Excel row convention to one worksheet frame."""
    reserved = set(LINEAGE_COLUMNS).intersection(frame.columns)
    if reserved:
        raise ValueError(f"Source worksheet contains reserved columns: {sorted(reserved)}")

    result = frame.copy(deep=False)
    result.insert(0, "source_row_number", range(2, len(result) + 2))
    result.insert(0, "source_sheet", sheet_name)
    result.insert(
        2,
        "source_row_id",
        build_source_row_ids(
            result["source_sheet"],
            result["source_row_number"],
            index=result.index,
        ),
    )
    return result


def fingerprint_source(path: Path) -> SourceFingerprint:
    stat = path.stat()
    return SourceFingerprint(
        size_bytes=stat.st_size,
        modified_time_ns=stat.st_mtime_ns,
        sha256=sha256(path),
    )


def assert_source_unchanged(path: Path, before: SourceFingerprint) -> SourceFingerprint:
    """Fail the batch if the workbook changes during processing."""
    after = fingerprint_source(path)
    if after != before:
        raise AssertionError("The source workbook changed during Phase 3 processing.")
    return after


def extract_workbook(
    path: Path = RAW_DATA_PATH,
    *,
    expected_sha256: str | None = SOURCE_WORKBOOK_SHA256,
) -> ExtractedWorkbook:
    """Read every worksheet exactly once and preserve every source occurrence."""
    if not path.is_file():
        raise FileNotFoundError(
            f"Source workbook not found: {path}. Place the unchanged UCI workbook there."
        )

    fingerprint = fingerprint_source(path)
    if expected_sha256 and fingerprint.sha256.upper() != expected_sha256.upper():
        raise ValueError(
            "Source workbook SHA-256 does not match the workbook approved in Phase 2. "
            f"Expected {expected_sha256.upper()}, found {fingerprint.sha256.upper()}."
        )

    frames: list[pd.DataFrame] = []
    row_counts: dict[str, int] = {}
    read_counts: dict[str, int] = {}

    with pd.ExcelFile(path, engine="openpyxl") as workbook:
        sheet_names = tuple(str(name) for name in workbook.sheet_names)
        if not sheet_names:
            raise ValueError("The source workbook contains no worksheets.")

        for sheet_name in sheet_names:
            print(f"[extract] Loading worksheet: {sheet_name}", flush=True)
            frame = workbook.parse(sheet_name=sheet_name)
            read_counts[sheet_name] = read_counts.get(sheet_name, 0) + 1

            actual_columns = list(frame.columns)
            if actual_columns != EXPECTED_COLUMNS:
                raise ValueError(
                    f"Unexpected schema in worksheet {sheet_name!r}. "
                    f"Expected {EXPECTED_COLUMNS}, found {actual_columns}."
                )

            row_counts[sheet_name] = len(frame)
            frames.append(attach_source_lineage(frame, sheet_name))

    if any(count != 1 for count in read_counts.values()):
        raise AssertionError("At least one worksheet was read more than once.")

    combined = pd.concat(frames, ignore_index=True, copy=False)
    expected_rows = sum(row_counts.values())
    if len(combined) != expected_rows:
        raise AssertionError(
            f"Worksheet rows ({expected_rows}) do not reconcile to combined rows "
            f"({len(combined)})."
        )
    if combined["source_row_id"].duplicated().any():
        raise AssertionError("A source_row_id collision was detected during extraction.")

    assert_source_unchanged(path, fingerprint)
    return ExtractedWorkbook(
        frame=combined,
        sheet_names=sheet_names,
        row_counts=row_counts,
        fingerprint=fingerprint,
    )

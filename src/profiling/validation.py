"""Write Phase 1 artifacts and validate the output contract."""

from __future__ import annotations

from os import stat_result

import pandas as pd

from ..config import DOCS_DIR, PROFILING_OUTPUT_DIR, PROFILING_REPORT_PATH, RAW_DATA_PATH
from .common import (
    CSV_OUTPUT_NAMES,
    DUPLICATE_DISCLAIMER,
    REQUIRED_REPORT_HEADINGS,
    SAMPLE_SIZE,
    sha256,
)


def write_and_validate(
    outputs: dict[str, pd.DataFrame],
    report: str,
    stat_before: stat_result,
    hash_before: str,
) -> str:
    if set(outputs) != set(CSV_OUTPUT_NAMES):
        raise AssertionError("The profiler output set does not match the Phase 1 contract.")

    print("[write] Writing profiling outputs", flush=True)
    PROFILING_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    for file_name, output in outputs.items():
        output.to_csv(
            PROFILING_OUTPUT_DIR / file_name,
            index=False,
            encoding="utf-8",
            lineterminator="\n",
        )
    PROFILING_REPORT_PATH.write_text(report, encoding="utf-8", newline="\n")

    print("[validate] Checking output structure and source immutability", flush=True)
    for file_name, expected in outputs.items():
        output_path = PROFILING_OUTPUT_DIR / file_name
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise AssertionError(f"Profiling output is missing or empty: {output_path}")
        reloaded = pd.read_csv(output_path)
        if any(str(column).startswith("Unnamed:") for column in reloaded.columns):
            raise AssertionError(f"Unexpected CSV index column in {file_name}.")
        if list(reloaded.columns) != list(expected.columns):
            raise AssertionError(f"CSV columns changed while writing {file_name}.")

    for file_name in ["extreme_quantity_samples.csv", "price_anomaly_samples.csv"]:
        sample = outputs[file_name]
        if not sample.empty and sample.groupby("sample_type").size().gt(SAMPLE_SIZE).any():
            raise AssertionError(f"A sample in {file_name} exceeds the configured limit.")

    saved_report = PROFILING_REPORT_PATH.read_text(encoding="utf-8")
    for heading in REQUIRED_REPORT_HEADINGS:
        if heading not in saved_report:
            raise AssertionError(f"Missing report heading: {heading}")
    if DUPLICATE_DISCLAIMER not in saved_report:
        raise AssertionError("The duplicate-looking disclaimer is missing from the report.")

    stat_after = RAW_DATA_PATH.stat()
    hash_after = sha256(RAW_DATA_PATH)
    if (
        hash_after != hash_before
        or stat_after.st_size != stat_before.st_size
        or stat_after.st_mtime_ns != stat_before.st_mtime_ns
    ):
        raise AssertionError("The source workbook changed during profiling.")
    return hash_after

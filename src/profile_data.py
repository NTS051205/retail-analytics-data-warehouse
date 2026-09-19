"""Run Phase 1 profiling against the unchanged Online Retail II workbook.

This entry point only coordinates loading, metric calculation, reporting, and
validation. Domain logic lives in ``src.profiling`` so each area can be tested
and debugged independently.
"""

from __future__ import annotations

from .config import PROFILING_REPORT_PATH, RAW_DATA_PATH
from .profiling.analysis import build_profiles
from .profiling.common import sha256
from .profiling.loader import load_workbook
from .profiling.report import build_report
from .profiling.validation import write_and_validate


def run_profile() -> None:
    if not RAW_DATA_PATH.is_file():
        raise FileNotFoundError(
            f"Source workbook not found: {RAW_DATA_PATH}. "
            "Place the unchanged UCI workbook at this exact path."
        )

    print(f"[start] Profiling {RAW_DATA_PATH}", flush=True)
    stat_before = RAW_DATA_PATH.stat()
    hash_before = sha256(RAW_DATA_PATH)

    source = load_workbook()
    outputs, analysis = build_profiles(source, stat_before, hash_before)
    report = build_report(analysis)
    hash_after = write_and_validate(outputs, report, stat_before, hash_before)

    print(
        f"[done] Profiled {len(source['frame']):,} rows "
        f"from {len(source['sheet_names'])} worksheets",
        flush=True,
    )
    print(f"[done] Source SHA-256 unchanged: {hash_after}", flush=True)
    print(f"[done] CSV outputs: {len(outputs)}", flush=True)
    print(f"[done] Report: {PROFILING_REPORT_PATH}", flush=True)


def main() -> None:
    run_profile()


if __name__ == "__main__":
    main()

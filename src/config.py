"""Minimal project paths used by the Phase 1 profiler."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DATA_PATH = PROJECT_ROOT / "data" / "raw" / "online_retail_II.xlsx"
PROFILING_OUTPUT_DIR = PROJECT_ROOT / "data" / "profiling"
DOCS_DIR = PROJECT_ROOT / "docs"
PROFILING_REPORT_PATH = DOCS_DIR / "data_profiling.md"

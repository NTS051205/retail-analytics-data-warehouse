"""Project paths shared by the local profiling and transformation phases."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DATA_PATH = PROJECT_ROOT / "data" / "raw" / "online_retail_II.xlsx"
PROFILING_OUTPUT_DIR = PROJECT_ROOT / "data" / "profiling"
PROCESSED_DATA_DIR = PROJECT_ROOT / "data" / "processed"
TRANSACTIONS_PARQUET_DIR = PROCESSED_DATA_DIR / "transactions"
BENCHMARK_OUTPUT_DIR = PROCESSED_DATA_DIR / "benchmark"
PHASE3_SUMMARY_PATH = PROCESSED_DATA_DIR / "phase3_summary.json"
REJECTED_DATA_DIR = PROJECT_ROOT / "data" / "rejected"
REJECTED_TRANSACTIONS_PATH = REJECTED_DATA_DIR / "transactions_rejected.csv"
DOCS_DIR = PROJECT_ROOT / "docs"
PROFILING_REPORT_PATH = DOCS_DIR / "data_profiling.md"
CSV_PARQUET_REPORT_PATH = DOCS_DIR / "csv_vs_parquet.md"

# The Phase 2 contract is frozen against this exact workbook.
SOURCE_WORKBOOK_SHA256 = (
    "BCBE73B35F5B7BABF197FB0CB983A11F5D9FF929078D4AA53D171B1F2DF2E980"
)

"""Small local CSV-versus-Parquet benchmark for the accepted Phase 3 data."""

from __future__ import annotations

import gc
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd
import pyarrow

from .config import BENCHMARK_OUTPUT_DIR, CSV_PARQUET_REPORT_PATH


DEFAULT_SAMPLE_ROWS = 100_000
DEFAULT_REPEATS = 3
SELECTED_COLUMNS = ["source_row_id", "invoice_no", "quantity", "line_amount"]


def _timed_read(
    reader: Callable[[], pd.DataFrame],
    *,
    expected_rows: int,
    repeats: int,
) -> tuple[float, list[float]]:
    measurements: list[float] = []
    for _ in range(repeats):
        gc.collect()
        started = time.perf_counter()
        loaded = reader()
        elapsed = time.perf_counter() - started
        if len(loaded) != expected_rows:
            raise AssertionError(
                f"Benchmark read returned {len(loaded)} rows; expected {expected_rows}."
            )
        measurements.append(elapsed)
        del loaded
    return statistics.median(measurements), measurements


def _format_seconds(value: float) -> str:
    return f"{value:.6f}"


def _format_mib(size_bytes: int) -> str:
    return f"{size_bytes / (1024 * 1024):.3f}"


def _render_report(result: dict[str, object]) -> str:
    csv_size = int(result["csv_size_bytes"])
    parquet_size = int(result["parquet_size_bytes"])
    size_ratio = parquet_size / csv_size if csv_size else 0.0

    return "\n".join(
        [
            "# CSV vs Parquet Benchmark",
            "",
            "## Scope",
            "",
            "This is a small local educational benchmark. It is not a universal performance claim.",
            "",
            f"- Measured at (UTC): `{result['measured_at_utc']}`",
            f"- Accepted rows available: `{int(result['accepted_rows_available']):,}`",
            f"- Rows measured: `{int(result['sample_rows']):,}`",
            f"- Columns in the full-read comparison: `{int(result['column_count'])}`",
            f"- Sample method: `{result['sample_method']}`",
            f"- Timed read repetitions: `{int(result['repeats'])}`; the table reports the median.",
            "",
            "## Environment",
            "",
            f"- Platform: `{result['platform']}`",
            f"- Python: `{result['python_version']}`",
            f"- Pandas: `{result['pandas_version']}`",
            f"- PyArrow: `{result['pyarrow_version']}`",
            "",
            "## Measured results",
            "",
            "| Measurement | CSV | Parquet |",
            "| --- | ---: | ---: |",
            f"| File size (bytes) | {csv_size:,} | {parquet_size:,} |",
            f"| File size (MiB) | {_format_mib(csv_size)} | {_format_mib(parquet_size)} |",
            "| Full read, median seconds | "
            f"{_format_seconds(float(result['csv_full_read_seconds']))} | "
            f"{_format_seconds(float(result['parquet_full_read_seconds']))} |",
            f"| Selected-column read ({', '.join(SELECTED_COLUMNS)}), median seconds | "
            f"{_format_seconds(float(result['csv_selected_read_seconds']))} | "
            f"{_format_seconds(float(result['parquet_selected_read_seconds']))} |",
            "",
            f"Parquet size divided by CSV size for this sample: `{size_ratio:.4f}`.",
            "",
            "Raw timing observations in seconds:",
            "",
            f"- CSV full read: `{result['csv_full_read_runs_seconds']}`",
            f"- Parquet full read: `{result['parquet_full_read_runs_seconds']}`",
            f"- CSV selected-column read: `{result['csv_selected_read_runs_seconds']}`",
            f"- Parquet selected-column read: `{result['parquet_selected_read_runs_seconds']}`",
            "",
            "## Limitations",
            "",
            "- The benchmark uses the first accepted source occurrences as a deterministic sample; it is not a randomized workload.",
            "- Operating-system file caching, other local processes, storage hardware, and library versions can affect timings.",
            "- CSV type inference and Parquet schema restoration do different work, so the formats are not semantically identical read paths.",
            "- The benchmark measures local single-process Pandas reads and does not predict every SQL Server or BI workload.",
            "",
            "## Project decision",
            "",
            "The project continues with Parquet for cleaned local analytical data because it preserves a stable typed schema, supports column projection, provides compression, and works with the required year/month partition layout. The measurements above are local evidence, not a guarantee that Parquet is faster in every environment.",
            "",
        ]
    )


def run_csv_parquet_benchmark(
    accepted: pd.DataFrame,
    *,
    output_dir: Path = BENCHMARK_OUTPUT_DIR,
    report_path: Path = CSV_PARQUET_REPORT_PATH,
    sample_rows: int = DEFAULT_SAMPLE_ROWS,
    repeats: int = DEFAULT_REPEATS,
) -> dict[str, object]:
    """Measure real local size and read behavior on one deterministic sample."""
    if accepted.empty:
        raise ValueError("The CSV-versus-Parquet benchmark requires accepted rows.")
    if sample_rows <= 0 or repeats <= 0:
        raise ValueError("sample_rows and repeats must be positive integers.")

    missing_columns = [column for column in SELECTED_COLUMNS if column not in accepted]
    if missing_columns:
        raise ValueError(f"Benchmark input is missing columns: {missing_columns}")

    measured_rows = min(sample_rows, len(accepted))
    sample = accepted.iloc[:measured_rows].copy()
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "accepted_sample.csv"
    parquet_path = output_dir / "accepted_sample.parquet"

    sample.to_csv(csv_path, index=False, encoding="utf-8", lineterminator="\n")
    sample.to_parquet(
        parquet_path,
        index=False,
        engine="pyarrow",
        compression="snappy",
    )

    csv_full, csv_full_runs = _timed_read(
        lambda: pd.read_csv(csv_path),
        expected_rows=measured_rows,
        repeats=repeats,
    )
    parquet_full, parquet_full_runs = _timed_read(
        lambda: pd.read_parquet(parquet_path, engine="pyarrow"),
        expected_rows=measured_rows,
        repeats=repeats,
    )
    csv_selected, csv_selected_runs = _timed_read(
        lambda: pd.read_csv(csv_path, usecols=SELECTED_COLUMNS),
        expected_rows=measured_rows,
        repeats=repeats,
    )
    parquet_selected, parquet_selected_runs = _timed_read(
        lambda: pd.read_parquet(
            parquet_path,
            columns=SELECTED_COLUMNS,
            engine="pyarrow",
        ),
        expected_rows=measured_rows,
        repeats=repeats,
    )

    result: dict[str, object] = {
        "measured_at_utc": datetime.now(timezone.utc).isoformat(),
        "accepted_rows_available": len(accepted),
        "sample_rows": measured_rows,
        "column_count": len(sample.columns),
        "sample_method": "first accepted rows in preserved source order",
        "repeats": repeats,
        "selected_columns": SELECTED_COLUMNS,
        "platform": platform.platform(),
        "python_version": sys.version.split()[0],
        "pandas_version": pd.__version__,
        "pyarrow_version": pyarrow.__version__,
        "csv_path": str(csv_path),
        "parquet_path": str(parquet_path),
        "csv_size_bytes": csv_path.stat().st_size,
        "parquet_size_bytes": parquet_path.stat().st_size,
        "csv_full_read_seconds": csv_full,
        "parquet_full_read_seconds": parquet_full,
        "csv_selected_read_seconds": csv_selected,
        "parquet_selected_read_seconds": parquet_selected,
        "csv_full_read_runs_seconds": csv_full_runs,
        "parquet_full_read_runs_seconds": parquet_full_runs,
        "csv_selected_read_runs_seconds": csv_selected_runs,
        "parquet_selected_read_runs_seconds": parquet_selected_runs,
    }
    report_path.write_text(_render_report(result), encoding="utf-8", newline="\n")
    return result

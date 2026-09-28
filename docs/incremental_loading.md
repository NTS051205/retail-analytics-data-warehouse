# Phase 6: historical monthly replay

Historical Online Retail II data is replayed chronologically as monthly batches to demonstrate incremental batch ingestion, idempotency, sequencing, and watermark management. This is a **static historical dataset**, not a live feed, CDC, or real-time ingestion.

Phase 4/5 already performed and validated a full historical bootstrap: `stg.transactions` and `dw.fact_sales` each contain 1,067,366 accepted physical source occurrences. Phase 6 does not delete or reload that bootstrap. On the current database, a first monthly replay normally inserts zero staging and fact rows because those same `source_row_id` values are already present. The Phase 4/5 initial loads proved the real insertion paths; Phase 6 demonstrates monthly control flow and failure-safe progress.

## Input and sequencing

- CLI period format: `YYYY-MM`, internally represented by the first `DATE` of that month.
- Accepted Parquet partitions: `data/processed/transactions/year=YYYY/month=MM/`, from `2009-12` through `2011-12`. `check-period` reads only the requested partition's Parquet files and verifies schema, row identity, month assignment, and nonzero count.
- The first normal run must be `2009-12` when the watermark is `NULL`; every later normal run must be the immediately following month. Gaps and periods outside the source range fail before mutation.
- A normal run at or behind the watermark reports `already processed` and changes nothing.
- `--force` is only for a period already at or behind the watermark. It rechecks/repairs idempotent rows but never moves the watermark backward or permits a future gap.

`ops.pipeline_watermark` has one project row named `retail_monthly_replay`, with `last_processed_period DATE NULL` and `updated_at_utc DATETIME2(6)`. `init` creates it only if absent; it never resets an existing watermark.

The watermark answers **“How far has this pipeline successfully progressed?”** `source_row_id` uniqueness in staging and fact answers **“Has this physical source occurrence already been loaded?”** The watermark is not a deduplication key.

## One monthly run

1. Validate the requested period and inspect that one Parquet partition before any write.
2. Read the watermark and enforce chronological order (or the restricted force-rerun rule).
3. Reuse the Phase 4 parameterized staging loader for only that partition. It commits bounded batches and skips IDs already present.
4. Upsert only the period's date/customer/product/country members and missing fact rows with [004_upsert_warehouse_period.sql](../sql/operations/004_upsert_warehouse_period.sql). Product descriptions use the exact Phase 2/5 core-sale-first, frequency, latest-date, BIN2 lexical ranking and `UNKNOWN DESCRIPTION` fallback. No SCD Type 2 or marts are created.
5. Run read-only [005_validate_period.sql](../sql/operations/005_validate_period.sql). Require Parquet ↔ staging ↔ fact counts, unique IDs, matching ID sets and signed monetary totals, zero negative accepted prices, zero FK orphans, and matching core commercial/cancellation line counts.
6. **Only after all checks pass**, atomically compare-and-set the watermark. A failure before this last step leaves it unchanged. Already committed staging batches remain safe for a later retry.

`--simulate-failure-before-watermark` is an explicit development-only option. It performs the normal load and quality checks, then deliberately raises an error before the watermark update. It is never enabled by default. Do not use it as a routine production-like run.

## Manual execution (CMD)

The user runs every test and database-changing command. First review the files, then:

```bat
cd /d E:\retail-analytics-data-warehouse
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m src.pipeline check-period --period 2009-12
.venv\Scripts\python.exe -m src.pipeline status
.venv\Scripts\python.exe -m src.pipeline init
.venv\Scripts\python.exe -m src.pipeline status
```

After confirming the initialized watermark is `NULL`, demonstrate success, controlled failure, recovery, and rerun:

```bat
.venv\Scripts\python.exe -m src.pipeline run --period 2009-12
.venv\Scripts\python.exe -m src.pipeline status
.venv\Scripts\python.exe -m src.pipeline run --period 2010-01 --simulate-failure-before-watermark
.venv\Scripts\python.exe -m src.pipeline status
.venv\Scripts\python.exe -m src.pipeline run --period 2010-01
.venv\Scripts\python.exe -m src.pipeline status
.venv\Scripts\python.exe -m src.pipeline run --period 2010-01
.venv\Scripts\python.exe -m src.pipeline run --period 2010-01 --force
.venv\Scripts\python.exe -m src.pipeline status
git status --short --untracked-files=all
```

The simulated-failure command intentionally exits with an error. The watermark should remain `2009-12` afterward; the normal retry should advance it to `2010-01`. The normal repeated `2010-01` run should report `already processed`. The forced rerun should insert no duplicates and leave the watermark at `2010-01`. The full bootstrap totals must remain 1,067,366 staging and fact rows.

For an independent read-only SSMS check, run [003_validate_watermark.sql](../sql/operations/003_validate_watermark.sql) against `RetailAnalytics`. SQL Server is local Developer Edition and is not containerized. No Airflow or scheduler is part of this phase.

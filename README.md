# Retail Analytics Data Warehouse

## Project

Retail Analytics Data Warehouse is a portfolio project for building a batch retail analytics pipeline.

## Goal

Transform the original Online Retail II workbook into reliable, analytics-ready data in a local Microsoft SQL Server warehouse for reporting in Power BI.

## Planned pipeline

```text
Online Retail II
-> Python/Pandas
-> Parquet
-> SQL Server
-> Power BI
```

Apache Airflow will be introduced later to orchestrate the pipeline after the manual workflow is working.

## Current status

Phase 0 — Project Foundation

The project uses SQL Server 2022 Developer Edition installed locally as the default instance and administered with SQL Server Management Studio (SSMS). SQL Server is not containerized.

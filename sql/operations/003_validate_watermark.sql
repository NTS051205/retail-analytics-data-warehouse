-- Read-only inspection after init or any period run.
SET NOCOUNT ON;
IF DB_NAME() <> N'RetailAnalytics'
    THROW 53010, 'Connect to RetailAnalytics before validating ops.', 1;
IF OBJECT_ID(N'ops.pipeline_watermark', N'U') IS NULL
    THROW 53011, 'ops.pipeline_watermark does not exist.', 1;

SELECT pipeline_name, last_processed_period, updated_at_utc,
       CASE WHEN last_processed_period IS NULL
                 OR DAY(last_processed_period) = 1 THEN 1 ELSE 0 END
           AS valid_first_day
FROM ops.pipeline_watermark
WHERE pipeline_name = N'retail_monthly_replay';

SELECT COUNT_BIG(*) AS project_watermark_rows,
       CONVERT(BIGINT, 1) AS expected_rows
FROM ops.pipeline_watermark
WHERE pipeline_name = N'retail_monthly_replay';

-- The existing full bootstrap must remain intact after monthly replay.
SELECT (SELECT COUNT_BIG(*) FROM stg.transactions) AS staging_rows,
       (SELECT COUNT_BIG(DISTINCT source_row_id) FROM stg.transactions)
           AS staging_distinct_source_row_ids,
       (SELECT COUNT_BIG(*) FROM dw.fact_sales) AS fact_rows,
       (SELECT COUNT_BIG(DISTINCT source_row_id) FROM dw.fact_sales)
           AS fact_distinct_source_row_ids,
       CONVERT(BIGINT, 1067366) AS validated_bootstrap_rows;

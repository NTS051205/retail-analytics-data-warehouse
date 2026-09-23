-- Phase 4 read-only reconciliation. Open in SSMS against RetailAnalytics.
-- Run after the first load and again after the identical second load.
SET NOCOUNT ON;

IF DB_NAME() <> N'RetailAnalytics'
    THROW 51010, 'Connect to RetailAnalytics before validating staging.', 1;

IF OBJECT_ID(N'stg.transactions', N'U') IS NULL
    THROW 51011, 'stg.transactions does not exist.', 1;

-- Source count and physical identity: both must equal 1,067,366.
SELECT COUNT_BIG(*) AS total_rows,
       COUNT_BIG(DISTINCT source_row_id) AS distinct_source_row_ids,
       CONVERT(BIGINT, 1067366) AS expected_rows
FROM stg.transactions;

-- Must return zero rows.
SELECT source_row_id, COUNT_BIG(*) AS occurrence_count
FROM stg.transactions
GROUP BY source_row_id
HAVING COUNT_BIG(*) > 1;

-- Only the two approved accepted classifications may appear.
SELECT classification, COUNT_BIG(*) AS actual_rows,
       CASE classification
           WHEN N'ACCEPT' THEN CONVERT(BIGINT, 824293)
           WHEN N'ACCEPT WITH QUALITY FLAG' THEN CONVERT(BIGINT, 243073)
           ELSE NULL
       END AS expected_rows
FROM stg.transactions
GROUP BY classification
ORDER BY classification;

-- Flag totals can overlap because one accepted row may carry several flags.
SELECT
    COALESCE(SUM(CONVERT(BIGINT, MISSING_CUSTOMER)), 0) AS MISSING_CUSTOMER,
    CONVERT(BIGINT, 243002) AS expected_MISSING_CUSTOMER,
    COALESCE(SUM(CONVERT(BIGINT, INVALID_CUSTOMER_ID)), 0) AS INVALID_CUSTOMER_ID,
    CONVERT(BIGINT, 0) AS expected_INVALID_CUSTOMER_ID,
    COALESCE(SUM(CONVERT(BIGINT, MISSING_DESCRIPTION)), 0) AS MISSING_DESCRIPTION,
    CONVERT(BIGINT, 4382) AS expected_MISSING_DESCRIPTION,
    COALESCE(SUM(CONVERT(BIGINT, ZERO_PRICE)), 0) AS ZERO_PRICE,
    CONVERT(BIGINT, 6202) AS expected_ZERO_PRICE,
    COALESCE(SUM(CONVERT(BIGINT, QUANTITY_SIGN_MISMATCH)), 0) AS QUANTITY_SIGN_MISMATCH,
    CONVERT(BIGINT, 3458) AS expected_QUANTITY_SIGN_MISMATCH,
    COALESCE(SUM(CONVERT(BIGINT, NONSTANDARD_INVOICE)), 0) AS NONSTANDARD_INVOICE,
    CONVERT(BIGINT, 1) AS expected_NONSTANDARD_INVOICE,
    COALESCE(SUM(CONVERT(BIGINT, MISSING_COUNTRY)), 0) AS MISSING_COUNTRY,
    CONVERT(BIGINT, 0) AS expected_MISSING_COUNTRY
FROM stg.transactions;

SELECT MIN(invoice_datetime) AS minimum_invoice_datetime,
       MAX(invoice_datetime) AS maximum_invoice_datetime,
       MIN(batch_month) AS minimum_batch_month,
       CONVERT(DATE, '2009-12-01') AS expected_minimum_batch_month,
       MAX(batch_month) AS maximum_batch_month,
       CONVERT(DATE, '2011-12-01') AS expected_maximum_batch_month
FROM stg.transactions;

-- Negative-price accounting adjustments were rejected in Phase 3.
SELECT COUNT_BIG(*) AS negative_price_rows,
       CONVERT(BIGINT, 0) AS expected_negative_price_rows
FROM stg.transactions
WHERE unit_price < 0;

-- Data-quality reconciliation only: these are not marts or dashboard KPIs.
SELECT
    COALESCE(SUM(CASE
        WHEN classification IN (N'ACCEPT', N'ACCEPT WITH QUALITY FLAG')
         AND NONSTANDARD_INVOICE = 0
         AND is_cancelled = 0
         AND quantity > 0
         AND unit_price > 0
        THEN CONVERT(BIGINT, 1) ELSE CONVERT(BIGINT, 0)
    END), 0) AS core_commercial_line_count,
    CONVERT(BIGINT, 1041669) AS expected_core_commercial_line_count,
    COALESCE(SUM(CASE
        WHEN classification IN (N'ACCEPT', N'ACCEPT WITH QUALITY FLAG')
         AND is_cancelled = 1
         AND LEN(invoice_no) > 1
         AND LEFT(invoice_no, 1) COLLATE Latin1_General_100_BIN2 = N'C'
         AND SUBSTRING(invoice_no, 2, LEN(invoice_no) - 1)
             COLLATE Latin1_General_100_BIN2 NOT LIKE N'%[^0-9]%'
         AND quantity < 0
         AND unit_price > 0
        THEN CONVERT(BIGINT, 1) ELSE CONVERT(BIGINT, 0)
    END), 0) AS core_cancellation_line_count,
    CONVERT(BIGINT, 19493) AS expected_core_cancellation_line_count
FROM stg.transactions;

-- Representative real SQL rows for comparison with accepted Parquet.
-- Each SELECT returns the lexically smallest source_row_id in its category.
SELECT TOP (1) N'normal_clean' AS sample_type, source_row_id, invoice_no,
       stock_code, description, quantity, unit_price, line_amount, customer_id,
       is_cancelled, classification, quality_flags
FROM stg.transactions
WHERE classification = N'ACCEPT' AND is_cancelled = 0
  AND quantity > 0 AND unit_price > 0
ORDER BY source_row_id;

SELECT TOP (1) N'missing_customer' AS sample_type, source_row_id, invoice_no,
       stock_code, description, quantity, unit_price, line_amount, customer_id,
       is_cancelled, classification, quality_flags
FROM stg.transactions
WHERE MISSING_CUSTOMER = 1
ORDER BY source_row_id;

SELECT TOP (1) N'zero_price' AS sample_type, source_row_id, invoice_no,
       stock_code, description, quantity, unit_price, line_amount, customer_id,
       is_cancelled, classification, quality_flags
FROM stg.transactions
WHERE ZERO_PRICE = 1
ORDER BY source_row_id;

SELECT TOP (1) N'non_c_negative_quantity' AS sample_type, source_row_id, invoice_no,
       stock_code, description, quantity, unit_price, line_amount, customer_id,
       is_cancelled, classification, quality_flags
FROM stg.transactions
WHERE is_cancelled = 0 AND quantity < 0 AND QUANTITY_SIGN_MISMATCH = 1
ORDER BY source_row_id;

SELECT TOP (1) N'cancellation' AS sample_type, source_row_id, invoice_no,
       stock_code, description, quantity, unit_price, line_amount, customer_id,
       is_cancelled, classification, quality_flags
FROM stg.transactions
WHERE is_cancelled = 1 AND quantity < 0
ORDER BY source_row_id;

SELECT TOP (1) N'missing_description' AS sample_type, source_row_id, invoice_no,
       stock_code, description, quantity, unit_price, line_amount, customer_id,
       is_cancelled, classification, quality_flags
FROM stg.transactions
WHERE MISSING_DESCRIPTION = 1
ORDER BY source_row_id;

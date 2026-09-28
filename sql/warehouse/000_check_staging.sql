-- Read-only Phase 5 preflight. Run in SSMS before 001_create_dw.sql.
-- These are actual SQL Server observations, not estimates from profiling.
SET NOCOUNT ON;

IF DB_NAME() <> N'RetailAnalytics'
    THROW 51900, 'Connect to RetailAnalytics for the staging preflight.', 1;
IF OBJECT_ID(N'stg.transactions', N'U') IS NULL
    THROW 51901, 'Validated stg.transactions is missing.', 1;

SELECT c.column_id, c.name AS column_name, ty.name AS sql_type,
       c.max_length AS storage_bytes, c.precision, c.scale, c.is_nullable,
       c.collation_name
FROM sys.columns AS c
JOIN sys.types AS ty ON ty.user_type_id = c.user_type_id
WHERE c.object_id = OBJECT_ID(N'stg.transactions', N'U')
ORDER BY c.column_id;

SELECT COUNT_BIG(*) AS staging_rows,
       COUNT_BIG(DISTINCT source_row_id) AS distinct_source_row_ids,
       MIN(invoice_datetime) AS minimum_invoice_datetime,
       MAX(invoice_datetime) AS maximum_invoice_datetime,
       COUNT_BIG(DISTINCT customer_id) AS known_customer_ids,
       COUNT_BIG(DISTINCT stock_code) AS stock_codes,
       COUNT_BIG(DISTINCT country COLLATE Latin1_General_100_BIN2)
           AS known_country_labels,
       SUM(CASE WHEN customer_id IS NULL THEN CONVERT(BIGINT, 1) ELSE 0 END)
           AS null_customer_rows,
       SUM(CASE WHEN country IS NULL THEN CONVERT(BIGINT, 1) ELSE 0 END)
           AS null_country_rows
FROM stg.transactions;

-- Bounded dimension/fact strings. NVARCHAR sizing is in UTF-16 code units.
SELECT MAX(DATALENGTH(stock_code) / 2) AS maximum_stock_code_units,
       MAX(DATALENGTH(description) / 2) AS maximum_description_units,
       MAX(DATALENGTH(customer_id) / 2) AS maximum_customer_id_units,
       MAX(DATALENGTH(country) / 2) AS maximum_country_units
FROM stg.transactions;

-- The approved canonical-description rule must handle both multiple names
-- and StockCodes with no non-null candidate. Actual values may differ from
-- the Phase 1 source evidence after Phase 3 rejected rows are removed.
;WITH description_counts AS
(
    SELECT stock_code,
           COUNT_BIG(DISTINCT description COLLATE Latin1_General_100_BIN2)
               AS distinct_nonnull_descriptions
    FROM stg.transactions
    GROUP BY stock_code
)
SELECT SUM(CASE WHEN distinct_nonnull_descriptions > 1
                THEN CONVERT(BIGINT, 1) ELSE 0 END)
           AS multi_description_stock_codes,
       SUM(CASE WHEN distinct_nonnull_descriptions = 0
                THEN CONVERT(BIGINT, 1) ELSE 0 END)
           AS no_description_stock_codes
FROM description_counts;

-- Read-only Phase 6 period reconciliation. pyodbc parameter: DATE (month start).
-- All counts and monetary values are measured, never hard-coded by month.
SET NOCOUNT ON;
DECLARE @period DATE = CONVERT(DATE, ?);
IF DB_NAME() <> N'RetailAnalytics'
    THROW 53200, 'Connect to RetailAnalytics before period validation.', 1;
IF @period IS NULL OR DAY(@period) <> 1
    THROW 53201, 'Requested period must be a first-of-month date.', 1;

;WITH staging_agg AS
(
    SELECT COUNT_BIG(*) AS staging_rows,
           COUNT_BIG(DISTINCT source_row_id) AS staging_distinct_ids,
           COALESCE(SUM(line_amount), CONVERT(DECIMAL(38,4), 0))
               AS staging_line_amount_sum,
           SUM(CASE WHEN unit_price < 0 THEN CONVERT(BIGINT, 1) ELSE 0 END)
               AS staging_negative_price_rows,
           SUM(CASE WHEN YEAR(invoice_datetime) <> YEAR(@period)
                         OR MONTH(invoice_datetime) <> MONTH(@period)
                    THEN CONVERT(BIGINT, 1) ELSE 0 END)
               AS staging_wrong_invoice_month_rows,
           SUM(CASE WHEN classification IN (N'ACCEPT', N'ACCEPT WITH QUALITY FLAG')
                         AND NONSTANDARD_INVOICE = 0 AND is_cancelled = 0
                         AND quantity > 0 AND unit_price > 0
                    THEN CONVERT(BIGINT, 1) ELSE 0 END)
               AS staging_core_commercial_lines,
           SUM(CASE WHEN classification IN (N'ACCEPT', N'ACCEPT WITH QUALITY FLAG')
                         AND is_cancelled = 1 AND quantity < 0 AND unit_price > 0
                         AND LEN(invoice_no) > 1
                         AND LEFT(invoice_no, 1) COLLATE Latin1_General_100_BIN2 = N'C'
                         AND SUBSTRING(invoice_no, 2, LEN(invoice_no) - 1)
                             COLLATE Latin1_General_100_BIN2 NOT LIKE N'%[^0-9]%'
                    THEN CONVERT(BIGINT, 1) ELSE 0 END)
               AS staging_core_cancellation_lines
    FROM stg.transactions
    WHERE batch_month = @period
),
fact_agg AS
(
    SELECT COUNT_BIG(*) AS fact_rows,
           COUNT_BIG(DISTINCT source_row_id) AS fact_distinct_ids,
           COALESCE(SUM(line_amount), CONVERT(DECIMAL(38,4), 0))
               AS fact_line_amount_sum,
           SUM(CASE WHEN unit_price < 0 THEN CONVERT(BIGINT, 1) ELSE 0 END)
               AS fact_negative_price_rows,
           SUM(CASE WHEN YEAR(invoice_datetime) <> YEAR(@period)
                         OR MONTH(invoice_datetime) <> MONTH(@period)
                    THEN CONVERT(BIGINT, 1) ELSE 0 END)
               AS fact_wrong_invoice_month_rows,
           SUM(CASE WHEN classification IN (N'ACCEPT', N'ACCEPT WITH QUALITY FLAG')
                         AND NONSTANDARD_INVOICE = 0 AND is_cancelled = 0
                         AND quantity > 0 AND unit_price > 0
                    THEN CONVERT(BIGINT, 1) ELSE 0 END)
               AS fact_core_commercial_lines,
           SUM(CASE WHEN classification IN (N'ACCEPT', N'ACCEPT WITH QUALITY FLAG')
                         AND is_cancelled = 1 AND quantity < 0 AND unit_price > 0
                         AND LEN(invoice_no) > 1
                         AND LEFT(invoice_no, 1) COLLATE Latin1_General_100_BIN2 = N'C'
                         AND SUBSTRING(invoice_no, 2, LEN(invoice_no) - 1)
                             COLLATE Latin1_General_100_BIN2 NOT LIKE N'%[^0-9]%'
                    THEN CONVERT(BIGINT, 1) ELSE 0 END)
               AS fact_core_cancellation_lines
    FROM dw.fact_sales
    WHERE batch_month = @period
),
foreign_key_agg AS
(
    SELECT COALESCE(SUM(CASE WHEN d.date_key IS NULL THEN CONVERT(BIGINT, 1) ELSE 0 END), 0)
               AS orphan_date_keys,
           COALESCE(SUM(CASE WHEN c.customer_key IS NULL THEN CONVERT(BIGINT, 1) ELSE 0 END), 0)
               AS orphan_customer_keys,
           COALESCE(SUM(CASE WHEN p.product_key IS NULL THEN CONVERT(BIGINT, 1) ELSE 0 END), 0)
               AS orphan_product_keys,
           COALESCE(SUM(CASE WHEN co.country_key IS NULL THEN CONVERT(BIGINT, 1) ELSE 0 END), 0)
               AS orphan_country_keys
    FROM dw.fact_sales AS f
    LEFT JOIN dw.dim_date AS d ON d.date_key = f.date_key
    LEFT JOIN dw.dim_customer AS c ON c.customer_key = f.customer_key
    LEFT JOIN dw.dim_product AS p ON p.product_key = f.product_key
    LEFT JOIN dw.dim_country AS co ON co.country_key = f.country_key
    WHERE f.batch_month = @period
),
identity_agg AS
(
    SELECT
        (SELECT COUNT_BIG(*) FROM stg.transactions AS s
         WHERE s.batch_month = @period
           AND NOT EXISTS
               (SELECT 1 FROM dw.fact_sales AS f
                WHERE f.source_row_id = s.source_row_id
                  AND f.batch_month = @period)) AS staging_ids_missing_fact,
        (SELECT COUNT_BIG(*) FROM dw.fact_sales AS f
         WHERE f.batch_month = @period
           AND NOT EXISTS
               (SELECT 1 FROM stg.transactions AS s
                WHERE s.source_row_id = f.source_row_id
                  AND s.batch_month = @period)) AS fact_ids_missing_staging
),
copied_values AS
(
    SELECT COALESCE(SUM(CASE WHEN f.invoice_no COLLATE Latin1_General_100_BIN2
                                      <> s.invoice_no COLLATE Latin1_General_100_BIN2
                                 OR f.invoice_datetime <> s.invoice_datetime
                                 OR f.batch_month <> s.batch_month
                                 OR f.quantity <> s.quantity
                                 OR f.unit_price <> s.unit_price
                                 OR f.line_amount <> s.line_amount
                                 OR f.is_cancelled <> s.is_cancelled
                                 OR f.NONSTANDARD_INVOICE <> s.NONSTANDARD_INVOICE
                            THEN CONVERT(BIGINT, 1) ELSE 0 END), 0)
               AS copied_value_mismatches,
           COALESCE(SUM(CASE WHEN d.date_key IS NULL OR p.product_key IS NULL
                                 OR (s.customer_id IS NOT NULL AND c.customer_key IS NULL)
                                 OR (s.country IS NOT NULL AND co.country_key IS NULL)
                                 OR f.date_key <> d.date_key
                                 OR f.customer_key <> CASE WHEN s.customer_id IS NULL
                                                           THEN 0 ELSE c.customer_key END
                                 OR f.product_key <> p.product_key
                                 OR f.country_key <> CASE WHEN s.country IS NULL
                                                          THEN 0 ELSE co.country_key END
                            THEN CONVERT(BIGINT, 1) ELSE 0 END), 0)
               AS dimension_mapping_mismatches
    FROM stg.transactions AS s
    JOIN dw.fact_sales AS f ON f.source_row_id = s.source_row_id
    LEFT JOIN dw.dim_date AS d ON d.full_date = CONVERT(DATE, s.invoice_datetime)
    LEFT JOIN dw.dim_customer AS c
      ON c.customer_id = s.customer_id COLLATE Latin1_General_100_BIN2
    LEFT JOIN dw.dim_product AS p ON p.stock_code = s.stock_code
    LEFT JOIN dw.dim_country AS co
      ON co.country = s.country COLLATE Latin1_General_100_BIN2
    WHERE s.batch_month = @period AND f.batch_month = @period
)
SELECT @period AS period,
       s.staging_rows, f.fact_rows,
       s.staging_distinct_ids, f.fact_distinct_ids,
       i.staging_ids_missing_fact, i.fact_ids_missing_staging,
       k.orphan_date_keys, k.orphan_customer_keys,
       k.orphan_product_keys, k.orphan_country_keys,
       s.staging_negative_price_rows, f.fact_negative_price_rows,
       s.staging_wrong_invoice_month_rows, f.fact_wrong_invoice_month_rows,
       s.staging_line_amount_sum, f.fact_line_amount_sum,
       s.staging_core_commercial_lines, f.fact_core_commercial_lines,
       s.staging_core_cancellation_lines, f.fact_core_cancellation_lines,
       v.copied_value_mismatches, v.dimension_mapping_mismatches
FROM staging_agg AS s
CROSS JOIN fact_agg AS f
CROSS JOIN foreign_key_agg AS k
CROSS JOIN identity_agg AS i
CROSS JOIN copied_values AS v;

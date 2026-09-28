-- Phase 5 read-only validation. Run in SSMS after initial load and rerun.
-- All expected dimension counts derive from actual validated staging values.
SET NOCOUNT ON;

IF DB_NAME() <> N'RetailAnalytics'
    THROW 52300, 'Connect to RetailAnalytics before validating the warehouse.', 1;
IF OBJECT_ID(N'dw.fact_sales', N'U') IS NULL
    THROW 52301, 'Warehouse fact table is missing.', 1;

-- Dimension grain/count reconciliation; none of these counts is guessed.
SELECT N'dim_date' AS dimension_name,
       (SELECT COUNT_BIG(*) FROM dw.dim_date) AS actual_rows,
       DATEDIFF(DAY,
           (SELECT CONVERT(DATE, MIN(invoice_datetime)) FROM stg.transactions),
           (SELECT CONVERT(DATE, MAX(invoice_datetime)) FROM stg.transactions)) + 1
           AS expected_from_staging
UNION ALL
SELECT N'dim_customer', (SELECT COUNT_BIG(*) FROM dw.dim_customer),
       (SELECT COUNT_BIG(DISTINCT customer_id) + 1 FROM stg.transactions)
UNION ALL
SELECT N'dim_product', (SELECT COUNT_BIG(*) FROM dw.dim_product),
       (SELECT COUNT_BIG(DISTINCT stock_code) FROM stg.transactions)
UNION ALL
SELECT N'dim_country', (SELECT COUNT_BIG(*) FROM dw.dim_country),
       (SELECT COUNT_BIG(DISTINCT country COLLATE Latin1_General_100_BIN2) + 1
        FROM stg.transactions);

-- Date coverage and deterministic date attributes.
SELECT MIN(full_date) AS minimum_date,
       MAX(full_date) AS maximum_date,
       COUNT_BIG(*) AS calendar_days,
       SUM(CASE WHEN date_key <> YEAR(full_date) * 10000
                                 + MONTH(full_date) * 100 + DAY(full_date)
                THEN CONVERT(BIGINT, 1) ELSE CONVERT(BIGINT, 0) END)
           AS incorrect_date_keys
FROM dw.dim_date;

-- Physical identity. First two values must equal 1,067,366; final query zero.
SELECT (SELECT COUNT_BIG(*) FROM stg.transactions) AS staging_rows,
       COUNT_BIG(*) AS fact_rows,
       COUNT_BIG(DISTINCT source_row_id) AS distinct_fact_source_row_ids,
       CONVERT(BIGINT, 1067366) AS expected_fact_rows
FROM dw.fact_sales;
SELECT source_row_id, COUNT_BIG(*) AS occurrence_count
FROM dw.fact_sales
GROUP BY source_row_id
HAVING COUNT_BIG(*) > 1;

-- Natural-key duplicates: every query must return zero rows.
SELECT customer_id, COUNT_BIG(*) AS occurrence_count
FROM dw.dim_customer WHERE customer_id IS NOT NULL
GROUP BY customer_id HAVING COUNT_BIG(*) > 1;
SELECT stock_code, COUNT_BIG(*) AS occurrence_count
FROM dw.dim_product GROUP BY stock_code HAVING COUNT_BIG(*) > 1;
SELECT country, COUNT_BIG(*) AS occurrence_count
FROM dw.dim_country WHERE country IS NOT NULL
GROUP BY country HAVING COUNT_BIG(*) > 1;
SELECT full_date, COUNT_BIG(*) AS occurrence_count
FROM dw.dim_date GROUP BY full_date HAVING COUNT_BIG(*) > 1;

SELECT (SELECT COUNT_BIG(*) FROM dw.dim_customer
        WHERE customer_key = 0 AND customer_id IS NULL) AS unknown_customer_members,
       (SELECT COUNT_BIG(*) FROM dw.dim_country
        WHERE country_key = 0 AND country IS NULL) AS unknown_country_members;

-- Foreign-key orphans must all be zero. SQL FKs protect future writes too.
SELECT SUM(CASE WHEN d.date_key IS NULL THEN CONVERT(BIGINT, 1) ELSE 0 END)
           AS orphan_date_keys,
       SUM(CASE WHEN c.customer_key IS NULL THEN CONVERT(BIGINT, 1) ELSE 0 END)
           AS orphan_customer_keys,
       SUM(CASE WHEN p.product_key IS NULL THEN CONVERT(BIGINT, 1) ELSE 0 END)
           AS orphan_product_keys,
       SUM(CASE WHEN co.country_key IS NULL THEN CONVERT(BIGINT, 1) ELSE 0 END)
           AS orphan_country_keys
FROM dw.fact_sales AS f
LEFT JOIN dw.dim_date AS d ON d.date_key = f.date_key
LEFT JOIN dw.dim_customer AS c ON c.customer_key = f.customer_key
LEFT JOIN dw.dim_product AS p ON p.product_key = f.product_key
LEFT JOIN dw.dim_country AS co ON co.country_key = f.country_key;

-- UNKNOWN usage is compared to actual NULLs in staging. The Phase 4 count
-- 243,002 is shown as a secondary expected value, not used as a substitute.
SELECT (SELECT COUNT_BIG(*) FROM dw.fact_sales WHERE customer_key = 0)
           AS unknown_customer_facts,
       (SELECT COUNT_BIG(*) FROM stg.transactions WHERE customer_id IS NULL)
           AS expected_from_staging,
       CONVERT(BIGINT, 243002) AS validated_phase4_missing_customer_rows,
       (SELECT COUNT_BIG(*) FROM dw.fact_sales WHERE country_key = 0)
           AS unknown_country_facts,
       (SELECT COUNT_BIG(*) FROM stg.transactions WHERE country IS NULL)
           AS expected_unknown_country_from_staging;

-- Both anti-joins must be zero: there must be a one-to-one identity mapping.
SELECT (SELECT COUNT_BIG(*) FROM stg.transactions AS s
        WHERE NOT EXISTS (SELECT 1 FROM dw.fact_sales AS f
                          WHERE f.source_row_id = s.source_row_id))
           AS staging_rows_missing_fact,
       (SELECT COUNT_BIG(*) FROM dw.fact_sales AS f
        WHERE NOT EXISTS (SELECT 1 FROM stg.transactions AS s
                          WHERE s.source_row_id = f.source_row_id))
           AS fact_rows_missing_staging;

-- This is load fidelity, not Net Sales: include all accepted signed lines.
SELECT (SELECT SUM(line_amount) FROM stg.transactions) AS staging_line_amount_sum,
       (SELECT SUM(line_amount) FROM dw.fact_sales) AS fact_line_amount_sum;

-- Also compare each matched line's source values and resolved dimension keys.
-- All counts must be zero; this catches errors that aggregate sums can hide.
SELECT
    SUM(CASE WHEN f.invoice_no COLLATE Latin1_General_100_BIN2
                    <> s.invoice_no COLLATE Latin1_General_100_BIN2
               OR f.invoice_datetime <> s.invoice_datetime
               OR f.source_sheet COLLATE Latin1_General_100_BIN2
                    <> s.source_sheet COLLATE Latin1_General_100_BIN2
               OR f.source_row_number <> s.source_row_number
               OR f.quantity <> s.quantity
               OR f.unit_price <> s.unit_price
               OR f.line_amount <> s.line_amount
               OR f.is_cancelled <> s.is_cancelled
               OR f.batch_month <> s.batch_month
               OR f.loaded_at <> s.loaded_at
               OR f.classification COLLATE Latin1_General_100_BIN2
                    <> s.classification COLLATE Latin1_General_100_BIN2
               OR f.quality_flags COLLATE Latin1_General_100_BIN2
                    <> s.quality_flags COLLATE Latin1_General_100_BIN2
               OR f.MISSING_CUSTOMER <> s.MISSING_CUSTOMER
               OR f.INVALID_CUSTOMER_ID <> s.INVALID_CUSTOMER_ID
               OR f.MISSING_DESCRIPTION <> s.MISSING_DESCRIPTION
               OR f.ZERO_PRICE <> s.ZERO_PRICE
               OR f.QUANTITY_SIGN_MISMATCH <> s.QUANTITY_SIGN_MISMATCH
               OR f.NONSTANDARD_INVOICE <> s.NONSTANDARD_INVOICE
               OR f.MISSING_COUNTRY <> s.MISSING_COUNTRY
             THEN CONVERT(BIGINT, 1) ELSE 0 END) AS copied_value_mismatches,
    SUM(CASE WHEN d.date_key IS NULL
               OR (s.customer_id IS NOT NULL AND c.customer_key IS NULL)
               OR p.product_key IS NULL
               OR (s.country IS NOT NULL AND co.country_key IS NULL)
               OR f.date_key <> d.date_key
               OR f.customer_key <> CASE WHEN s.customer_id IS NULL
                                          THEN 0 ELSE c.customer_key END
               OR f.product_key <> p.product_key
               OR f.country_key <> CASE WHEN s.country IS NULL
                                         THEN 0 ELSE co.country_key END
             THEN CONVERT(BIGINT, 1) ELSE 0 END) AS dimension_mapping_mismatches
FROM stg.transactions AS s
JOIN dw.fact_sales AS f ON f.source_row_id = s.source_row_id
LEFT JOIN dw.dim_date AS d
  ON d.full_date = CONVERT(DATE, s.invoice_datetime)
LEFT JOIN dw.dim_customer AS c
  ON c.customer_id = s.customer_id COLLATE Latin1_General_100_BIN2
LEFT JOIN dw.dim_product AS p ON p.stock_code = s.stock_code
LEFT JOIN dw.dim_country AS co
  ON co.country = s.country COLLATE Latin1_General_100_BIN2;

-- Classification/cancellation and the exact Phase 2 predicates. No mart is
-- created here. The C-invoice pattern is ordinal/case-sensitive.
SELECT
    (SELECT COUNT_BIG(*) FROM stg.transactions WHERE is_cancelled = 1)
        AS staging_cancelled_rows,
    (SELECT COUNT_BIG(*) FROM dw.fact_sales WHERE is_cancelled = 1)
        AS fact_cancelled_rows,
    (SELECT COUNT_BIG(*) FROM stg.transactions WHERE classification = N'ACCEPT')
        AS staging_accept_rows,
    (SELECT COUNT_BIG(*) FROM dw.fact_sales WHERE classification = N'ACCEPT')
        AS fact_accept_rows,
    (SELECT COUNT_BIG(*) FROM stg.transactions
     WHERE classification = N'ACCEPT WITH QUALITY FLAG')
        AS staging_flagged_rows,
    (SELECT COUNT_BIG(*) FROM dw.fact_sales
     WHERE classification = N'ACCEPT WITH QUALITY FLAG')
        AS fact_flagged_rows;

SELECT
    SUM(CASE WHEN classification IN (N'ACCEPT', N'ACCEPT WITH QUALITY FLAG')
                  AND NONSTANDARD_INVOICE = 0 AND is_cancelled = 0
                  AND quantity > 0 AND unit_price > 0
             THEN CONVERT(BIGINT, 1) ELSE 0 END) AS core_commercial_line_count,
    CONVERT(BIGINT, 1041669) AS expected_core_commercial_line_count,
    SUM(CASE WHEN classification IN (N'ACCEPT', N'ACCEPT WITH QUALITY FLAG')
                  AND is_cancelled = 1 AND quantity < 0 AND unit_price > 0
                  AND LEN(invoice_no) > 1
                  AND LEFT(invoice_no, 1) COLLATE Latin1_General_100_BIN2 = N'C'
                  AND SUBSTRING(invoice_no, 2, LEN(invoice_no) - 1)
                      COLLATE Latin1_General_100_BIN2 NOT LIKE N'%[^0-9]%'
             THEN CONVERT(BIGINT, 1) ELSE 0 END) AS core_cancellation_line_count,
    CONVERT(BIGINT, 19493) AS expected_core_cancellation_line_count
FROM dw.fact_sales;

-- Independent canonical-description recomputation from staging. Tier 0 is
-- non-null descriptions on core commercial lines; tier 1 is all accepted rows.
-- The BIN2 collation preserves exact description variants and lexical order.
DECLARE @description_stats TABLE
(
    stock_code NVARCHAR(64) COLLATE Latin1_General_100_BIN2 NOT NULL,
    description NVARCHAR(512) COLLATE Latin1_General_100_BIN2 NOT NULL,
    source_tier TINYINT NOT NULL,
    occurrence_count BIGINT NOT NULL,
    latest_invoice_datetime DATETIME2(3) NOT NULL
);

;WITH candidates AS
(
    SELECT stock_code, description COLLATE Latin1_General_100_BIN2 AS description,
           invoice_datetime, CONVERT(TINYINT, 0) AS source_tier
    FROM stg.transactions
    WHERE description IS NOT NULL AND NONSTANDARD_INVOICE = 0
      AND is_cancelled = 0 AND quantity > 0 AND unit_price > 0
      AND classification IN (N'ACCEPT', N'ACCEPT WITH QUALITY FLAG')
    UNION ALL
    SELECT stock_code, description COLLATE Latin1_General_100_BIN2,
           invoice_datetime, CONVERT(TINYINT, 1)
    FROM stg.transactions
    WHERE description IS NOT NULL
      AND classification IN (N'ACCEPT', N'ACCEPT WITH QUALITY FLAG')
)
INSERT INTO @description_stats
    (stock_code, description, source_tier, occurrence_count, latest_invoice_datetime)
SELECT stock_code, description, source_tier,
       COUNT_BIG(*), MAX(invoice_datetime)
FROM candidates
GROUP BY stock_code, description, source_tier;

DECLARE @expected_product TABLE
(
    stock_code NVARCHAR(64) COLLATE Latin1_General_100_BIN2 NOT NULL PRIMARY KEY,
    description NVARCHAR(512) COLLATE Latin1_General_100_BIN2 NOT NULL,
    source_tier TINYINT NULL,
    occurrence_count BIGINT NULL,
    latest_invoice_datetime DATETIME2(3) NULL
);

;WITH ranked AS
(
    SELECT stock_code, description, source_tier, occurrence_count,
           latest_invoice_datetime,
           ROW_NUMBER() OVER
           (
               PARTITION BY stock_code
               ORDER BY source_tier ASC, occurrence_count DESC,
                        latest_invoice_datetime DESC,
                        description COLLATE Latin1_General_100_BIN2 ASC
           ) AS rank_number
    FROM @description_stats
)
INSERT INTO @expected_product
    (stock_code, description, source_tier, occurrence_count, latest_invoice_datetime)
SELECT codes.stock_code, COALESCE(best.description, N'UNKNOWN DESCRIPTION'),
       best.source_tier, best.occurrence_count, best.latest_invoice_datetime
FROM (SELECT DISTINCT stock_code FROM stg.transactions) AS codes
LEFT JOIN ranked AS best
  ON best.stock_code = codes.stock_code AND best.rank_number = 1;

SELECT COUNT_BIG(*) AS canonical_product_mismatches
FROM @expected_product AS expected
FULL OUTER JOIN dw.dim_product AS actual
  ON actual.stock_code = expected.stock_code
WHERE expected.stock_code IS NULL OR actual.stock_code IS NULL
   OR actual.description COLLATE Latin1_General_100_BIN2
      <> expected.description COLLATE Latin1_General_100_BIN2;

-- Ten real multi-description StockCodes, their chosen text, frequency and
-- latest timestamp. The value 1,232 was Phase 1 source evidence, not assumed.
SELECT TOP (10) e.stock_code,
       e.description AS selected_canonical_description,
       e.source_tier AS selected_source_tier,
       e.occurrence_count,
       e.latest_invoice_datetime,
       p.description AS stored_dimension_description
FROM @expected_product AS e
JOIN dw.dim_product AS p ON p.stock_code = e.stock_code
JOIN
(
    SELECT stock_code
    FROM @description_stats
    WHERE source_tier = 1
    GROUP BY stock_code
    HAVING COUNT_BIG(*) > 1
) AS multi ON multi.stock_code = e.stock_code
ORDER BY e.stock_code;

-- Six actual, deterministic staging/fact examples. A physical row can satisfy
-- multiple quality categories; categories need not have distinct source IDs.
DECLARE @samples TABLE
(
    sample_type NVARCHAR(40) NOT NULL PRIMARY KEY,
    source_row_id CHAR(64) COLLATE Latin1_General_100_BIN2 NOT NULL
);
INSERT INTO @samples (sample_type, source_row_id)
SELECT categories.sample_type, picked.source_row_id
FROM (VALUES
    (N'normal_clean'), (N'missing_customer'), (N'zero_price'),
    (N'non_c_negative_quantity'), (N'cancellation'), (N'missing_description')
) AS categories(sample_type)
CROSS APPLY
(
    SELECT TOP (1) s.source_row_id
    FROM stg.transactions AS s
    WHERE (categories.sample_type = N'normal_clean'
           AND s.classification = N'ACCEPT' AND s.is_cancelled = 0
           AND s.quantity > 0 AND s.unit_price > 0)
       OR (categories.sample_type = N'missing_customer'
           AND s.MISSING_CUSTOMER = 1)
       OR (categories.sample_type = N'zero_price' AND s.ZERO_PRICE = 1)
       OR (categories.sample_type = N'non_c_negative_quantity'
           AND s.is_cancelled = 0 AND s.quantity < 0
           AND s.QUANTITY_SIGN_MISMATCH = 1)
       OR (categories.sample_type = N'cancellation'
           AND s.is_cancelled = 1 AND s.quantity < 0)
       OR (categories.sample_type = N'missing_description'
           AND s.MISSING_DESCRIPTION = 1)
    ORDER BY s.source_row_id
) AS picked;

SELECT sample.sample_type, s.source_row_id,
       s.invoice_no AS staging_invoice_no, f.invoice_no AS fact_invoice_no,
       s.quantity AS staging_quantity, f.quantity AS fact_quantity,
       s.unit_price AS staging_unit_price, f.unit_price AS fact_unit_price,
       s.line_amount AS staging_line_amount, f.line_amount AS fact_line_amount,
       s.is_cancelled AS staging_is_cancelled, f.is_cancelled AS fact_is_cancelled,
       d.date_key AS expected_date_key, f.date_key AS fact_date_key,
       CASE WHEN s.customer_id IS NULL THEN 0 ELSE c.customer_key END
           AS expected_customer_key,
       f.customer_key AS fact_customer_key,
       p.product_key AS expected_product_key, f.product_key AS fact_product_key,
       CASE WHEN s.country IS NULL THEN 0 ELSE co.country_key END
           AS expected_country_key,
       f.country_key AS fact_country_key,
       CASE WHEN f.source_row_id IS NOT NULL
                 AND f.invoice_no COLLATE Latin1_General_100_BIN2
                     = s.invoice_no COLLATE Latin1_General_100_BIN2
                 AND f.quantity = s.quantity
                 AND f.unit_price = s.unit_price
                 AND f.line_amount = s.line_amount
                 AND f.is_cancelled = s.is_cancelled
                 AND f.date_key = d.date_key
                 AND f.customer_key = CASE WHEN s.customer_id IS NULL
                                           THEN 0 ELSE c.customer_key END
                 AND f.product_key = p.product_key
                 AND f.country_key = CASE WHEN s.country IS NULL
                                          THEN 0 ELSE co.country_key END
            THEN CONVERT(BIT, 1) ELSE CONVERT(BIT, 0) END AS sample_matches
FROM @samples AS sample
JOIN stg.transactions AS s ON s.source_row_id = sample.source_row_id
LEFT JOIN dw.fact_sales AS f ON f.source_row_id = s.source_row_id
LEFT JOIN dw.dim_date AS d ON d.full_date = CONVERT(DATE, s.invoice_datetime)
LEFT JOIN dw.dim_customer AS c
  ON c.customer_id = s.customer_id COLLATE Latin1_General_100_BIN2
LEFT JOIN dw.dim_product AS p ON p.stock_code = s.stock_code
LEFT JOIN dw.dim_country AS co
  ON co.country = s.country COLLATE Latin1_General_100_BIN2
ORDER BY sample.sample_type;

SELECT COUNT(*) AS found_sample_categories,
       CONVERT(INT, 6) AS expected_sample_categories
FROM @samples;

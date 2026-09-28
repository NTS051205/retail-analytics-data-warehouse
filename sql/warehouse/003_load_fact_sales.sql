-- Phase 5 initial/rerun-safe fact load. Run after 002_load_dimensions.sql.
-- One accepted staging occurrence becomes exactly one fact row. This is not
-- period-aware incremental logic; Phase 6 owns that concern.
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'RetailAnalytics'
    THROW 52200, 'Connect to RetailAnalytics before loading fact_sales.', 1;
IF OBJECT_ID(N'stg.transactions', N'U') IS NULL
   OR OBJECT_ID(N'dw.fact_sales', N'U') IS NULL
    THROW 52201, 'Staging or fact table is missing.', 1;
IF (SELECT COUNT_BIG(*) FROM stg.transactions) <> 1067366
    THROW 52202, 'Staging count differs from validated Phase 4.', 1;

-- These are data-resolution gates, not LEFT JOINs that silently discard lines.
IF NOT EXISTS (SELECT 1 FROM dw.dim_customer WHERE customer_key = 0 AND customer_id IS NULL)
    THROW 52203, 'UNKNOWN customer key 0 is missing.', 1;
IF NOT EXISTS (SELECT 1 FROM dw.dim_country WHERE country_key = 0 AND country IS NULL)
    THROW 52204, 'UNKNOWN country key 0 is missing.', 1;
IF EXISTS
(
    SELECT 1 FROM stg.transactions AS s
    LEFT JOIN dw.dim_date AS d
      ON d.full_date = CONVERT(DATE, s.invoice_datetime)
    WHERE d.date_key IS NULL
)
    THROW 52205, 'Some staging dates do not resolve to dim_date.', 1;
IF EXISTS
(
    SELECT 1 FROM stg.transactions AS s
    LEFT JOIN dw.dim_customer AS d
      ON d.customer_id = s.customer_id COLLATE Latin1_General_100_BIN2
    WHERE s.customer_id IS NOT NULL AND d.customer_key IS NULL
)
    THROW 52206, 'Some known staging customers do not resolve.', 1;
IF EXISTS
(
    SELECT 1 FROM stg.transactions AS s
    LEFT JOIN dw.dim_product AS d ON d.stock_code = s.stock_code
    WHERE d.product_key IS NULL
)
    THROW 52207, 'Some staging StockCodes do not resolve.', 1;
IF EXISTS
(
    SELECT 1 FROM stg.transactions AS s
    LEFT JOIN dw.dim_country AS d
      ON d.country = s.country COLLATE Latin1_General_100_BIN2
    WHERE s.country IS NOT NULL AND d.country_key IS NULL
)
    THROW 52208, 'Some staging country labels do not resolve.', 1;
IF EXISTS
(
    SELECT 1 FROM dw.fact_sales AS f
    LEFT JOIN stg.transactions AS s ON s.source_row_id = f.source_row_id
    WHERE s.source_row_id IS NULL
)
    THROW 52209, 'Existing fact rows are outside the validated staging set.', 1;

DECLARE @batch_rows INT = 0;
DECLARE @inserted BIGINT = 0;

BEGIN TRY
    WHILE 1 = 1
    BEGIN
        BEGIN TRANSACTION;

        ;WITH next_batch AS
        (
            SELECT TOP (5000) s.source_row_id
            FROM stg.transactions AS s
            WHERE NOT EXISTS
            (
                SELECT 1 FROM dw.fact_sales AS f
                WHERE f.source_row_id = s.source_row_id
            )
            ORDER BY s.source_row_id
        )
        INSERT INTO dw.fact_sales
        (
            source_row_id, source_sheet, source_row_number,
            invoice_no, invoice_datetime,
            date_key, customer_key, product_key, country_key,
            quantity, unit_price, line_amount, is_cancelled,
            batch_month, loaded_at, classification, quality_flags,
            MISSING_CUSTOMER, INVALID_CUSTOMER_ID, MISSING_DESCRIPTION,
            ZERO_PRICE, QUANTITY_SIGN_MISMATCH, NONSTANDARD_INVOICE,
            MISSING_COUNTRY
        )
        SELECT s.source_row_id, s.source_sheet, s.source_row_number,
               s.invoice_no, s.invoice_datetime,
               d.date_key,
               CASE WHEN s.customer_id IS NULL THEN 0 ELSE c.customer_key END,
               p.product_key,
               CASE WHEN s.country IS NULL THEN 0 ELSE co.country_key END,
               s.quantity, s.unit_price, s.line_amount, s.is_cancelled,
               s.batch_month, s.loaded_at, s.classification, s.quality_flags,
               s.MISSING_CUSTOMER, s.INVALID_CUSTOMER_ID, s.MISSING_DESCRIPTION,
               s.ZERO_PRICE, s.QUANTITY_SIGN_MISMATCH, s.NONSTANDARD_INVOICE,
               s.MISSING_COUNTRY
        FROM next_batch AS b
        JOIN stg.transactions AS s ON s.source_row_id = b.source_row_id
        JOIN dw.dim_date AS d ON d.full_date = CONVERT(DATE, s.invoice_datetime)
        LEFT JOIN dw.dim_customer AS c
          ON c.customer_id = s.customer_id COLLATE Latin1_General_100_BIN2
        JOIN dw.dim_product AS p ON p.stock_code = s.stock_code
        LEFT JOIN dw.dim_country AS co
          ON co.country = s.country COLLATE Latin1_General_100_BIN2;

        SET @batch_rows = @@ROWCOUNT;
        COMMIT TRANSACTION;
        SET @inserted = @inserted + @batch_rows;

        IF @batch_rows = 0 BREAK;
    END;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;

DECLARE @fact_rows BIGINT = (SELECT COUNT_BIG(*) FROM dw.fact_sales);
DECLARE @staging_rows BIGINT = (SELECT COUNT_BIG(*) FROM stg.transactions);
IF @fact_rows <> @staging_rows
    THROW 52210, 'Fact/staging row counts differ; committed batches can be resumed safely.', 1;

SELECT @staging_rows AS processed_staging_rows,
       @inserted AS inserted_this_run,
       @staging_rows - @inserted AS skipped_existing,
       @fact_rows AS fact_rows;

-- Parameterized Phase 6 warehouse upsert for one accepted batch_month.
-- pyodbc parameters: requested first-of-month DATE, source maximum period DATE.
-- Reuses the validated Phase 5 keys, grains, UNKNOWN members and canonical
-- product-description ranking. It does not create another warehouse.
SET NOCOUNT ON;
SET XACT_ABORT ON;
DECLARE @period DATE = CONVERT(DATE, ?);
DECLARE @source_max_period DATE = CONVERT(DATE, ?);

IF DB_NAME() <> N'RetailAnalytics'
    THROW 53100, 'Connect to RetailAnalytics before warehouse period upsert.', 1;
IF @period IS NULL OR DAY(@period) <> 1 OR @source_max_period IS NULL
    THROW 53101, 'Period parameters must be first-of-month dates.', 1;
IF OBJECT_ID(N'dw.fact_sales', N'U') IS NULL
    THROW 53102, 'Validated Phase 5 warehouse is missing.', 1;
IF NOT EXISTS (SELECT 1 FROM dw.dim_customer WHERE customer_key = 0 AND customer_id IS NULL)
    THROW 53103, 'UNKNOWN customer key 0 is missing.', 1;
IF NOT EXISTS (SELECT 1 FROM dw.dim_country WHERE country_key = 0 AND country IS NULL)
    THROW 53104, 'UNKNOWN country key 0 is missing.', 1;

DECLARE @period_rows BIGINT =
    (SELECT COUNT_BIG(*) FROM stg.transactions WHERE batch_month = @period);
IF @period_rows = 0
    THROW 53105, 'No accepted staging rows exist for the requested period.', 1;

DECLARE @last_date DATE = CASE WHEN @period = @source_max_period
    THEN (SELECT CONVERT(DATE, MAX(invoice_datetime))
          FROM stg.transactions WHERE batch_month = @period)
    ELSE EOMONTH(@period) END;
DECLARE @date_inserted INT = 0;
DECLARE @customer_inserted INT = 0;
DECLARE @product_inserted INT = 0;
DECLARE @product_updated INT = 0;
DECLARE @country_inserted INT = 0;
DECLARE @fact_inserted INT = 0;

BEGIN TRY
    BEGIN TRANSACTION;

    -- Calendar coverage within this historical month; the final source
    -- month stops at its last actual invoice date, matching Phase 5.
    ;WITH calendar AS
    (
        SELECT @period AS full_date
        UNION ALL
        SELECT DATEADD(DAY, 1, full_date)
        FROM calendar
        WHERE full_date < @last_date
    )
    INSERT INTO dw.dim_date
        (date_key, full_date, [day], [month], month_name,
         [quarter], [year], weekday_name, is_weekend)
    SELECT YEAR(c.full_date) * 10000 + MONTH(c.full_date) * 100 + DAY(c.full_date),
           c.full_date, DAY(c.full_date), MONTH(c.full_date),
           CHOOSE(MONTH(c.full_date), N'January', N'February', N'March', N'April',
                  N'May', N'June', N'July', N'August', N'September', N'October',
                  N'November', N'December'),
           DATEPART(QUARTER, c.full_date), YEAR(c.full_date),
           CHOOSE(DATEDIFF(DAY, CONVERT(DATE, '19000101', 112), c.full_date) % 7 + 1,
                  N'Monday', N'Tuesday', N'Wednesday', N'Thursday',
                  N'Friday', N'Saturday', N'Sunday'),
           CASE WHEN DATEDIFF(DAY, CONVERT(DATE, '19000101', 112), c.full_date) % 7
                     IN (5, 6) THEN 1 ELSE 0 END
    FROM calendar AS c
    WHERE NOT EXISTS
        (SELECT 1 FROM dw.dim_date AS d WHERE d.full_date = c.full_date)
    OPTION (MAXRECURSION 0);
    SET @date_inserted = @@ROWCOUNT;

    INSERT INTO dw.dim_customer (customer_id)
    SELECT DISTINCT s.customer_id
    FROM stg.transactions AS s
    WHERE s.batch_month = @period AND s.customer_id IS NOT NULL
      AND NOT EXISTS
      (
          SELECT 1 FROM dw.dim_customer AS d
          WHERE d.customer_id = s.customer_id COLLATE Latin1_General_100_BIN2
      );
    SET @customer_inserted = @@ROWCOUNT;

    DECLARE @touched_products TABLE
    (
        stock_code NVARCHAR(64) COLLATE Latin1_General_100_BIN2 PRIMARY KEY
    );
    INSERT INTO @touched_products (stock_code)
    SELECT DISTINCT stock_code FROM stg.transactions WHERE batch_month = @period;

    DECLARE @canonical_product TABLE
    (
        stock_code NVARCHAR(64) COLLATE Latin1_General_100_BIN2 PRIMARY KEY,
        description NVARCHAR(512) NOT NULL
    );
    ;WITH candidates AS
    (
        SELECT s.stock_code,
               s.description COLLATE Latin1_General_100_BIN2 AS description,
               s.invoice_datetime, CONVERT(TINYINT, 0) AS source_tier
        FROM stg.transactions AS s
        JOIN @touched_products AS t ON t.stock_code = s.stock_code
        WHERE s.description IS NOT NULL
          AND s.classification IN (N'ACCEPT', N'ACCEPT WITH QUALITY FLAG')
          AND s.NONSTANDARD_INVOICE = 0 AND s.is_cancelled = 0
          AND s.quantity > 0 AND s.unit_price > 0
        UNION ALL
        SELECT s.stock_code,
               s.description COLLATE Latin1_General_100_BIN2,
               s.invoice_datetime, CONVERT(TINYINT, 1)
        FROM stg.transactions AS s
        JOIN @touched_products AS t ON t.stock_code = s.stock_code
        WHERE s.description IS NOT NULL
          AND s.classification IN (N'ACCEPT', N'ACCEPT WITH QUALITY FLAG')
    ),
    frequencies AS
    (
        SELECT stock_code, description, source_tier,
               COUNT_BIG(*) AS occurrence_count,
               MAX(invoice_datetime) AS latest_invoice_datetime
        FROM candidates
        GROUP BY stock_code, description, source_tier
    ),
    ranked AS
    (
        SELECT stock_code, description,
               ROW_NUMBER() OVER
               (
                   PARTITION BY stock_code
                   ORDER BY source_tier ASC, occurrence_count DESC,
                            latest_invoice_datetime DESC,
                            description COLLATE Latin1_General_100_BIN2 ASC
               ) AS rank_number
        FROM frequencies
    )
    INSERT INTO @canonical_product (stock_code, description)
    SELECT t.stock_code, COALESCE(r.description, N'UNKNOWN DESCRIPTION')
    FROM @touched_products AS t
    LEFT JOIN ranked AS r
      ON r.stock_code = t.stock_code AND r.rank_number = 1;

    INSERT INTO dw.dim_product (stock_code, description)
    SELECT c.stock_code, c.description
    FROM @canonical_product AS c
    WHERE NOT EXISTS
        (SELECT 1 FROM dw.dim_product AS p WHERE p.stock_code = c.stock_code);
    SET @product_inserted = @@ROWCOUNT;

    UPDATE p SET p.description = c.description
    FROM dw.dim_product AS p
    JOIN @canonical_product AS c ON c.stock_code = p.stock_code
    WHERE p.description COLLATE Latin1_General_100_BIN2
          <> c.description COLLATE Latin1_General_100_BIN2;
    SET @product_updated = @@ROWCOUNT;

    INSERT INTO dw.dim_country (country)
    SELECT DISTINCT s.country COLLATE Latin1_General_100_BIN2
    FROM stg.transactions AS s
    WHERE s.batch_month = @period AND s.country IS NOT NULL
      AND NOT EXISTS
      (
          SELECT 1 FROM dw.dim_country AS d
          WHERE d.country = s.country COLLATE Latin1_General_100_BIN2
      );
    SET @country_inserted = @@ROWCOUNT;

    -- Never let INNER JOINs silently discard a staged occurrence.
    IF EXISTS
    (
        SELECT 1 FROM stg.transactions AS s
        LEFT JOIN dw.dim_date AS d
          ON d.full_date = CONVERT(DATE, s.invoice_datetime)
        LEFT JOIN dw.dim_customer AS c
          ON c.customer_id = s.customer_id COLLATE Latin1_General_100_BIN2
        LEFT JOIN dw.dim_product AS p ON p.stock_code = s.stock_code
        LEFT JOIN dw.dim_country AS co
          ON co.country = s.country COLLATE Latin1_General_100_BIN2
        WHERE s.batch_month = @period
          AND (d.date_key IS NULL OR p.product_key IS NULL
               OR (s.customer_id IS NOT NULL AND c.customer_key IS NULL)
               OR (s.country IS NOT NULL AND co.country_key IS NULL))
    )
        THROW 53106, 'A period staging row cannot resolve its dimension keys.', 1;

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
    FROM stg.transactions AS s
    JOIN dw.dim_date AS d ON d.full_date = CONVERT(DATE, s.invoice_datetime)
    LEFT JOIN dw.dim_customer AS c
      ON c.customer_id = s.customer_id COLLATE Latin1_General_100_BIN2
    JOIN dw.dim_product AS p ON p.stock_code = s.stock_code
    LEFT JOIN dw.dim_country AS co
      ON co.country = s.country COLLATE Latin1_General_100_BIN2
    WHERE s.batch_month = @period
      AND NOT EXISTS
      (
          SELECT 1 FROM dw.fact_sales AS f
          WHERE f.source_row_id = s.source_row_id
      );
    SET @fact_inserted = @@ROWCOUNT;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;

SELECT @period AS period, @period_rows AS period_staging_rows,
       @date_inserted AS date_inserted,
       @customer_inserted AS customer_inserted,
       @product_inserted AS product_inserted,
       @product_updated AS product_updated,
       @country_inserted AS country_inserted,
       @fact_inserted AS fact_inserted,
       @period_rows - @fact_inserted AS fact_skipped_existing;

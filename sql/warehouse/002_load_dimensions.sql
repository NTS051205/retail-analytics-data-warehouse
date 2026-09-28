-- Phase 5 initial/rerun-safe dimension load. Execute only after 001_create_dw.sql.
-- No truncation and no SCD history. Product description follows the exact
-- Phase 2 rule: core-commercial candidates first, then all accepted rows;
-- frequency, latest invoice_datetime, then ordinal lexical order.
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'RetailAnalytics'
    THROW 52100, 'Connect to RetailAnalytics before loading dimensions.', 1;
IF OBJECT_ID(N'stg.transactions', N'U') IS NULL
    THROW 52101, 'Validated staging table is missing.', 1;
IF OBJECT_ID(N'dw.dim_date', N'U') IS NULL
   OR OBJECT_ID(N'dw.dim_customer', N'U') IS NULL
   OR OBJECT_ID(N'dw.dim_product', N'U') IS NULL
   OR OBJECT_ID(N'dw.dim_country', N'U') IS NULL
    THROW 52102, 'Create all four dw dimensions before loading them.', 1;
IF (SELECT COUNT_BIG(*) FROM stg.transactions) <> 1067366
    THROW 52103, 'Staging count differs from validated Phase 4; stop and investigate.', 1;

DECLARE @first_date DATE = (SELECT CONVERT(DATE, MIN(invoice_datetime)) FROM stg.transactions);
DECLARE @last_date DATE = (SELECT CONVERT(DATE, MAX(invoice_datetime)) FROM stg.transactions);
IF @first_date <> CONVERT(DATE, '20091201', 112)
   OR @last_date <> CONVERT(DATE, '20111209', 112)
    THROW 52104, 'Staging invoice date range differs from validated Phase 4.', 1;

DECLARE @customer_identity_on BIT = 0;
DECLARE @country_identity_on BIT = 0;

BEGIN TRY
    BEGIN TRANSACTION;

    -- 1. Calendar dates, including dates without any transaction. 1900-01-01
    -- was Monday, so modulo arithmetic avoids DATEFIRST and language settings.
    ;WITH calendar AS
    (
        SELECT @first_date AS full_date
        UNION ALL
        SELECT DATEADD(DAY, 1, full_date)
        FROM calendar
        WHERE full_date < @last_date
    )
    INSERT INTO dw.dim_date
        (date_key, full_date, [day], [month], month_name,
         [quarter], [year], weekday_name, is_weekend)
    SELECT YEAR(c.full_date) * 10000 + MONTH(c.full_date) * 100 + DAY(c.full_date),
           c.full_date,
           DAY(c.full_date), MONTH(c.full_date),
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
    (
        SELECT 1 FROM dw.dim_date AS d WHERE d.full_date = c.full_date
    )
    OPTION (MAXRECURSION 0);

    -- 2. Customer: one explicit UNKNOWN row with NULL business ID.
    IF NOT EXISTS (SELECT 1 FROM dw.dim_customer WHERE customer_key = 0)
    BEGIN
        SET IDENTITY_INSERT dw.dim_customer ON;
        SET @customer_identity_on = 1;
        INSERT INTO dw.dim_customer (customer_key, customer_id) VALUES (0, NULL);
        SET IDENTITY_INSERT dw.dim_customer OFF;
        SET @customer_identity_on = 0;
    END;

    INSERT INTO dw.dim_customer (customer_id)
    SELECT DISTINCT s.customer_id
    FROM stg.transactions AS s
    WHERE s.customer_id IS NOT NULL
      AND NOT EXISTS
      (
          SELECT 1 FROM dw.dim_customer AS d
          WHERE d.customer_id = s.customer_id COLLATE Latin1_General_100_BIN2
      );

    -- 3. Product: exact descriptions are grouped with BIN2 so SQL Server's
    -- database-default case-insensitive collation cannot collapse variants.
    DECLARE @canonical_product TABLE
    (
        stock_code NVARCHAR(64) COLLATE Latin1_General_100_BIN2 NOT NULL PRIMARY KEY,
        description NVARCHAR(512) NOT NULL
    );

    ;WITH candidates AS
    (
        SELECT s.stock_code,
               s.description COLLATE Latin1_General_100_BIN2 AS description,
               s.invoice_datetime,
               CONVERT(TINYINT, 0) AS source_tier
        FROM stg.transactions AS s
        WHERE s.description IS NOT NULL
          AND s.classification IN (N'ACCEPT', N'ACCEPT WITH QUALITY FLAG')
          AND s.NONSTANDARD_INVOICE = 0
          AND s.is_cancelled = 0
          AND s.quantity > 0
          AND s.unit_price > 0

        UNION ALL

        SELECT s.stock_code,
               s.description COLLATE Latin1_General_100_BIN2,
               s.invoice_datetime,
               CONVERT(TINYINT, 1)
        FROM stg.transactions AS s
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
    SELECT codes.stock_code,
           COALESCE(best.description, N'UNKNOWN DESCRIPTION')
    FROM (SELECT DISTINCT stock_code FROM stg.transactions) AS codes
    LEFT JOIN ranked AS best
      ON best.stock_code = codes.stock_code AND best.rank_number = 1;

    INSERT INTO dw.dim_product (stock_code, description)
    SELECT c.stock_code, c.description
    FROM @canonical_product AS c
    WHERE NOT EXISTS
    (
        SELECT 1 FROM dw.dim_product AS p WHERE p.stock_code = c.stock_code
    );

    -- A rerun also corrects an older canonical description if staging has
    -- changed, without changing product keys or creating SCD Type 2 rows.
    UPDATE p
       SET p.description = c.description
    FROM dw.dim_product AS p
    JOIN @canonical_product AS c ON c.stock_code = p.stock_code
    WHERE p.description COLLATE Latin1_General_100_BIN2
          <> c.description COLLATE Latin1_General_100_BIN2;

    -- 4. Country: explicit UNKNOWN is allowed even though currently unused.
    IF NOT EXISTS (SELECT 1 FROM dw.dim_country WHERE country_key = 0)
    BEGIN
        SET IDENTITY_INSERT dw.dim_country ON;
        SET @country_identity_on = 1;
        INSERT INTO dw.dim_country (country_key, country) VALUES (0, NULL);
        SET IDENTITY_INSERT dw.dim_country OFF;
        SET @country_identity_on = 0;
    END;

    INSERT INTO dw.dim_country (country)
    SELECT DISTINCT s.country COLLATE Latin1_General_100_BIN2
    FROM stg.transactions AS s
    WHERE s.country IS NOT NULL
      AND NOT EXISTS
      (
          SELECT 1 FROM dw.dim_country AS d
          WHERE d.country = s.country COLLATE Latin1_General_100_BIN2
      );

    IF (SELECT COUNT_BIG(*) FROM dw.dim_date)
       <> DATEDIFF(DAY, @first_date, @last_date) + 1
        THROW 52105, 'dim_date count does not cover the full staging range.', 1;
    IF (SELECT COUNT_BIG(*) FROM dw.dim_customer)
       <> (SELECT COUNT_BIG(DISTINCT customer_id) + 1 FROM stg.transactions)
        THROW 52106, 'dim_customer count does not match known IDs plus UNKNOWN.', 1;
    IF (SELECT COUNT_BIG(*) FROM dw.dim_product)
       <> (SELECT COUNT_BIG(DISTINCT stock_code) FROM stg.transactions)
        THROW 52107, 'dim_product count does not match distinct StockCodes.', 1;
    IF (SELECT COUNT_BIG(*) FROM dw.dim_country)
       <> (SELECT COUNT_BIG(DISTINCT country COLLATE Latin1_General_100_BIN2) + 1
           FROM stg.transactions)
        THROW 52108, 'dim_country count does not match labels plus UNKNOWN.', 1;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    IF @customer_identity_on = 1 SET IDENTITY_INSERT dw.dim_customer OFF;
    IF @country_identity_on = 1 SET IDENTITY_INSERT dw.dim_country OFF;
    THROW;
END CATCH;

SELECT N'dim_date' AS dimension_name, COUNT_BIG(*) AS row_count FROM dw.dim_date
UNION ALL SELECT N'dim_customer', COUNT_BIG(*) FROM dw.dim_customer
UNION ALL SELECT N'dim_product', COUNT_BIG(*) FROM dw.dim_product
UNION ALL SELECT N'dim_country', COUNT_BIG(*) FROM dw.dim_country;

-- Phase 4 staging grain: one accepted physical source invoice-line occurrence.
-- Measured accepted-Parquet maximum string lengths (2026-09-23):
-- source_sheet 14, invoice_no 7, stock_code 12, description 35,
-- customer_id 5, country 20, classification 24, quality_flags 70.
-- The bounded sizes below retain the approved Phase 2 target widths with headroom.
-- loaded_at is a UTC clock value stored in DATETIME2(6); the loader removes the
-- UTC tzinfo only after converting to UTC. Do not treat it as local server time.
-- No GO separator: this file can run in SSMS or through pyodbc --init.
SET NOCOUNT ON;

IF DB_NAME() <> N'RetailAnalytics'
    THROW 51001, 'Connect to RetailAnalytics before creating stg.transactions.', 1;

IF SCHEMA_ID(N'stg') IS NULL
    THROW 51002, 'The stg schema is missing. Run 001_create_staging_schema.sql first.', 1;

IF OBJECT_ID(N'stg.transactions', N'U') IS NULL
BEGIN
    CREATE TABLE stg.transactions
    (
        source_sheet             NVARCHAR(64) NOT NULL,
        source_row_number        BIGINT NOT NULL,
        source_row_id            CHAR(64) COLLATE Latin1_General_100_BIN2 NOT NULL,
        invoice_no               NVARCHAR(32) NOT NULL,
        stock_code               NVARCHAR(64) COLLATE Latin1_General_100_BIN2 NOT NULL,
        description              NVARCHAR(512) NULL,
        quantity                 INT NOT NULL,
        invoice_datetime         DATETIME2(3) NOT NULL,
        unit_price               DECIMAL(19,4) NOT NULL,
        customer_id              NVARCHAR(32) NULL,
        country                  NVARCHAR(128) NULL,
        line_amount              DECIMAL(30,4) NOT NULL,
        is_cancelled             BIT NOT NULL,
        batch_month              DATE NOT NULL,
        loaded_at                DATETIME2(6) NOT NULL,
        classification           NVARCHAR(32) NOT NULL,
        quality_flags            NVARCHAR(256) NOT NULL,
        MISSING_CUSTOMER         BIT NOT NULL,
        INVALID_CUSTOMER_ID      BIT NOT NULL,
        MISSING_DESCRIPTION      BIT NOT NULL,
        ZERO_PRICE               BIT NOT NULL,
        QUANTITY_SIGN_MISMATCH   BIT NOT NULL,
        NONSTANDARD_INVOICE      BIT NOT NULL,
        MISSING_COUNTRY          BIT NOT NULL,

        CONSTRAINT UQ_stg_transactions_source_row_id UNIQUE (source_row_id),
        CONSTRAINT CK_stg_transactions_source_row_number
            CHECK (source_row_number >= 2),
        CONSTRAINT CK_stg_transactions_classification
            CHECK (classification IN (N'ACCEPT', N'ACCEPT WITH QUALITY FLAG')),
        CONSTRAINT CK_stg_transactions_nonnegative_price
            CHECK (unit_price >= 0),
        CONSTRAINT CK_stg_transactions_batch_month
            CHECK (DAY(batch_month) = 1)
    );
END;

-- An existing table must match the complete approved column contract. Never
-- alter, drop, truncate, or repair it automatically when this check fails.
DECLARE @expected TABLE
(
    column_name SYSNAME PRIMARY KEY,
    type_name SYSNAME NOT NULL,
    max_chars SMALLINT NULL,
    numeric_precision TINYINT NULL,
    fractional_scale TINYINT NULL,
    is_nullable BIT NOT NULL,
    required_collation SYSNAME NULL
);

INSERT INTO @expected
    (column_name, type_name, max_chars, numeric_precision,
     fractional_scale, is_nullable, required_collation)
VALUES
    (N'source_sheet',           N'nvarchar',  64, NULL, NULL, 0, NULL),
    (N'source_row_number',      N'bigint',   NULL, NULL, NULL, 0, NULL),
    (N'source_row_id',          N'char',       64, NULL, NULL, 0, N'Latin1_General_100_BIN2'),
    (N'invoice_no',             N'nvarchar',  32, NULL, NULL, 0, NULL),
    (N'stock_code',             N'nvarchar',  64, NULL, NULL, 0, N'Latin1_General_100_BIN2'),
    (N'description',            N'nvarchar', 512, NULL, NULL, 1, NULL),
    (N'quantity',               N'int',      NULL, NULL, NULL, 0, NULL),
    (N'invoice_datetime',       N'datetime2',NULL, NULL,    3, 0, NULL),
    (N'unit_price',             N'decimal',  NULL,   19,    4, 0, NULL),
    (N'customer_id',            N'nvarchar',  32, NULL, NULL, 1, NULL),
    (N'country',                N'nvarchar', 128, NULL, NULL, 1, NULL),
    (N'line_amount',            N'decimal',  NULL,   30,    4, 0, NULL),
    (N'is_cancelled',           N'bit',      NULL, NULL, NULL, 0, NULL),
    (N'batch_month',            N'date',     NULL, NULL, NULL, 0, NULL),
    (N'loaded_at',              N'datetime2',NULL, NULL,    6, 0, NULL),
    (N'classification',         N'nvarchar',  32, NULL, NULL, 0, NULL),
    (N'quality_flags',          N'nvarchar', 256, NULL, NULL, 0, NULL),
    (N'MISSING_CUSTOMER',       N'bit',      NULL, NULL, NULL, 0, NULL),
    (N'INVALID_CUSTOMER_ID',    N'bit',      NULL, NULL, NULL, 0, NULL),
    (N'MISSING_DESCRIPTION',    N'bit',      NULL, NULL, NULL, 0, NULL),
    (N'ZERO_PRICE',             N'bit',      NULL, NULL, NULL, 0, NULL),
    (N'QUANTITY_SIGN_MISMATCH', N'bit',      NULL, NULL, NULL, 0, NULL),
    (N'NONSTANDARD_INVOICE',    N'bit',      NULL, NULL, NULL, 0, NULL),
    (N'MISSING_COUNTRY',        N'bit',      NULL, NULL, NULL, 0, NULL);

IF EXISTS
(
    SELECT column_name, type_name, max_chars, numeric_precision,
           fractional_scale, is_nullable, required_collation
    FROM @expected
    EXCEPT
    SELECT c.name, t.name,
           CASE WHEN t.name = N'nvarchar' THEN c.max_length / 2
                WHEN t.name = N'char' THEN c.max_length ELSE NULL END,
           CASE WHEN t.name = N'decimal' THEN c.precision ELSE NULL END,
           CASE WHEN t.name IN (N'decimal', N'datetime2') THEN c.scale ELSE NULL END,
           c.is_nullable,
           CASE WHEN c.name IN (N'source_row_id', N'stock_code')
                THEN c.collation_name ELSE NULL END
    FROM sys.columns AS c
    JOIN sys.types AS t ON t.user_type_id = c.user_type_id
    WHERE c.object_id = OBJECT_ID(N'stg.transactions', N'U')
)
OR EXISTS
(
    SELECT c.name, t.name,
           CASE WHEN t.name = N'nvarchar' THEN c.max_length / 2
                WHEN t.name = N'char' THEN c.max_length ELSE NULL END,
           CASE WHEN t.name = N'decimal' THEN c.precision ELSE NULL END,
           CASE WHEN t.name IN (N'decimal', N'datetime2') THEN c.scale ELSE NULL END,
           c.is_nullable,
           CASE WHEN c.name IN (N'source_row_id', N'stock_code')
                THEN c.collation_name ELSE NULL END
    FROM sys.columns AS c
    JOIN sys.types AS t ON t.user_type_id = c.user_type_id
    WHERE c.object_id = OBJECT_ID(N'stg.transactions', N'U')
    EXCEPT
    SELECT column_name, type_name, max_chars, numeric_precision,
           fractional_scale, is_nullable, required_collation
    FROM @expected
)
    THROW 51003, 'Existing stg.transactions columns differ from the Phase 4 contract. No data was changed.', 1;

IF NOT EXISTS
(
    SELECT 1
    FROM sys.indexes AS i
    JOIN sys.index_columns AS ic
      ON ic.object_id = i.object_id AND ic.index_id = i.index_id
    JOIN sys.columns AS c
      ON c.object_id = ic.object_id AND c.column_id = ic.column_id
    WHERE i.object_id = OBJECT_ID(N'stg.transactions', N'U')
      AND i.is_unique = 1
      AND i.is_disabled = 0
      AND i.has_filter = 0
      AND ic.key_ordinal > 0
    GROUP BY i.index_id
    HAVING COUNT(*) = 1 AND MAX(c.name) = N'source_row_id'
)
    THROW 51004, 'stg.transactions needs a unique source_row_id key. No data was changed.', 1;

SELECT OBJECT_SCHEMA_NAME(OBJECT_ID(N'stg.transactions')) AS schema_name,
       OBJECT_NAME(OBJECT_ID(N'stg.transactions')) AS table_name;

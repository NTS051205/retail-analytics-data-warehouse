-- Phase 5 DDL only. Run in RetailAnalytics; safe to rerun without dropping data.
-- Grain: one accepted source occurrence per fact row. All five tables use
-- explicit natural/physical uniqueness in addition to surrogate primary keys.
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'RetailAnalytics'
    THROW 52000, 'Connect to RetailAnalytics before creating dw objects.', 1;
IF OBJECT_ID(N'stg.transactions', N'U') IS NULL
    THROW 52001, 'Validated stg.transactions is required before Phase 5.', 1;

IF SCHEMA_ID(N'dw') IS NULL
    EXEC(N'CREATE SCHEMA dw');

-- Every calendar date in the staging range is loaded by 002_load_dimensions.sql.
IF OBJECT_ID(N'dw.dim_date', N'U') IS NULL
BEGIN
    CREATE TABLE dw.dim_date
    (
        date_key      INT NOT NULL CONSTRAINT PK_dw_dim_date PRIMARY KEY,
        full_date     DATE NOT NULL CONSTRAINT UQ_dw_dim_date_full_date UNIQUE,
        [day]         TINYINT NOT NULL,
        [month]       TINYINT NOT NULL,
        month_name    NVARCHAR(20) NOT NULL,
        [quarter]     TINYINT NOT NULL,
        [year]        SMALLINT NOT NULL,
        weekday_name  NVARCHAR(20) NOT NULL,
        is_weekend    BIT NOT NULL,
        CONSTRAINT CK_dw_dim_date_key
            CHECK (date_key = YEAR(full_date) * 10000 + MONTH(full_date) * 100 + DAY(full_date))
    );
END;

-- Key 0 is the one UNKNOWN member. NULL is not a fake customer business ID.
IF OBJECT_ID(N'dw.dim_customer', N'U') IS NULL
BEGIN
    CREATE TABLE dw.dim_customer
    (
        customer_key INT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_dw_dim_customer PRIMARY KEY,
        customer_id NVARCHAR(32) COLLATE Latin1_General_100_BIN2 NULL
            CONSTRAINT UQ_dw_dim_customer_customer_id UNIQUE,
        CONSTRAINT CK_dw_dim_customer_unknown CHECK
        (
            (customer_key = 0 AND customer_id IS NULL)
            OR (customer_key > 0 AND customer_id IS NOT NULL)
        )
    );
END;

-- Case-sensitive StockCode is mandatory. Description is the approved
-- deterministic display value, not a line-level overwrite of staging.
IF OBJECT_ID(N'dw.dim_product', N'U') IS NULL
BEGIN
    CREATE TABLE dw.dim_product
    (
        product_key INT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_dw_dim_product PRIMARY KEY,
        stock_code NVARCHAR(64) COLLATE Latin1_General_100_BIN2 NOT NULL
            CONSTRAINT UQ_dw_dim_product_stock_code UNIQUE,
        description NVARCHAR(512) NOT NULL
    );
END;

-- Keep source country labels exactly. Key 0 allows a future accepted NULL
-- country without treating the source label 'Unspecified' as UNKNOWN.
IF OBJECT_ID(N'dw.dim_country', N'U') IS NULL
BEGIN
    CREATE TABLE dw.dim_country
    (
        country_key INT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_dw_dim_country PRIMARY KEY,
        country NVARCHAR(128) COLLATE Latin1_General_100_BIN2 NULL
            CONSTRAINT UQ_dw_dim_country_country UNIQUE,
        CONSTRAINT CK_dw_dim_country_unknown CHECK
        (
            (country_key = 0 AND country IS NULL)
            OR (country_key > 0 AND country IS NOT NULL)
        )
    );
END;

-- Quality columns are deliberately retained: Phase 2 KPI eligibility needs
-- NONSTANDARD_INVOICE, and later quality analysis must not re-read staging.
-- loaded_at is UTC stored without an offset, matching Phase 4 staging.
IF OBJECT_ID(N'dw.fact_sales', N'U') IS NULL
BEGIN
    CREATE TABLE dw.fact_sales
    (
        sales_key BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_dw_fact_sales PRIMARY KEY,
        source_row_id CHAR(64) COLLATE Latin1_General_100_BIN2 NOT NULL
            CONSTRAINT UQ_dw_fact_sales_source_row_id UNIQUE,
        source_sheet NVARCHAR(64) NOT NULL,
        source_row_number BIGINT NOT NULL,
        invoice_no NVARCHAR(32) NOT NULL,
        invoice_datetime DATETIME2(3) NOT NULL,
        date_key INT NOT NULL,
        customer_key INT NOT NULL,
        product_key INT NOT NULL,
        country_key INT NOT NULL,
        quantity INT NOT NULL,
        unit_price DECIMAL(19,4) NOT NULL,
        line_amount DECIMAL(30,4) NOT NULL,
        is_cancelled BIT NOT NULL,
        batch_month DATE NOT NULL,
        loaded_at DATETIME2(6) NOT NULL,
        classification NVARCHAR(32) NOT NULL,
        quality_flags NVARCHAR(256) NOT NULL,
        MISSING_CUSTOMER BIT NOT NULL,
        INVALID_CUSTOMER_ID BIT NOT NULL,
        MISSING_DESCRIPTION BIT NOT NULL,
        ZERO_PRICE BIT NOT NULL,
        QUANTITY_SIGN_MISMATCH BIT NOT NULL,
        NONSTANDARD_INVOICE BIT NOT NULL,
        MISSING_COUNTRY BIT NOT NULL,
        CONSTRAINT FK_dw_fact_sales_date FOREIGN KEY (date_key)
            REFERENCES dw.dim_date(date_key),
        CONSTRAINT FK_dw_fact_sales_customer FOREIGN KEY (customer_key)
            REFERENCES dw.dim_customer(customer_key),
        CONSTRAINT FK_dw_fact_sales_product FOREIGN KEY (product_key)
            REFERENCES dw.dim_product(product_key),
        CONSTRAINT FK_dw_fact_sales_country FOREIGN KEY (country_key)
            REFERENCES dw.dim_country(country_key),
        CONSTRAINT CK_dw_fact_sales_source_row_number
            CHECK (source_row_number >= 2),
        CONSTRAINT CK_dw_fact_sales_classification
            CHECK (classification IN (N'ACCEPT', N'ACCEPT WITH QUALITY FLAG')),
        CONSTRAINT CK_dw_fact_sales_nonnegative_price
            CHECK (unit_price >= 0)
    );
END;

-- Refuse to proceed if an existing table differs. No automatic ALTER/DROP.
-- max_chars is UTF-16 code units for NVARCHAR, bytes for CHAR.
DECLARE @expected TABLE
(
    table_name SYSNAME NOT NULL,
    column_name SYSNAME NOT NULL,
    type_name SYSNAME NOT NULL,
    max_chars SMALLINT NULL,
    numeric_precision TINYINT NULL,
    fractional_scale TINYINT NULL,
    is_nullable BIT NOT NULL,
    is_identity BIT NOT NULL,
    required_collation SYSNAME NULL,
    PRIMARY KEY (table_name, column_name)
);

INSERT INTO @expected VALUES
    (N'dim_date', N'date_key', N'int', NULL, NULL, NULL, 0, 0, NULL),
    (N'dim_date', N'full_date', N'date', NULL, NULL, NULL, 0, 0, NULL),
    (N'dim_date', N'day', N'tinyint', NULL, NULL, NULL, 0, 0, NULL),
    (N'dim_date', N'month', N'tinyint', NULL, NULL, NULL, 0, 0, NULL),
    (N'dim_date', N'month_name', N'nvarchar', 20, NULL, NULL, 0, 0, NULL),
    (N'dim_date', N'quarter', N'tinyint', NULL, NULL, NULL, 0, 0, NULL),
    (N'dim_date', N'year', N'smallint', NULL, NULL, NULL, 0, 0, NULL),
    (N'dim_date', N'weekday_name', N'nvarchar', 20, NULL, NULL, 0, 0, NULL),
    (N'dim_date', N'is_weekend', N'bit', NULL, NULL, NULL, 0, 0, NULL),
    (N'dim_customer', N'customer_key', N'int', NULL, NULL, NULL, 0, 1, NULL),
    (N'dim_customer', N'customer_id', N'nvarchar', 32, NULL, NULL, 1, 0, N'Latin1_General_100_BIN2'),
    (N'dim_product', N'product_key', N'int', NULL, NULL, NULL, 0, 1, NULL),
    (N'dim_product', N'stock_code', N'nvarchar', 64, NULL, NULL, 0, 0, N'Latin1_General_100_BIN2'),
    (N'dim_product', N'description', N'nvarchar', 512, NULL, NULL, 0, 0, NULL),
    (N'dim_country', N'country_key', N'int', NULL, NULL, NULL, 0, 1, NULL),
    (N'dim_country', N'country', N'nvarchar', 128, NULL, NULL, 1, 0, N'Latin1_General_100_BIN2'),
    (N'fact_sales', N'sales_key', N'bigint', NULL, NULL, NULL, 0, 1, NULL),
    (N'fact_sales', N'source_row_id', N'char', 64, NULL, NULL, 0, 0, N'Latin1_General_100_BIN2'),
    (N'fact_sales', N'source_sheet', N'nvarchar', 64, NULL, NULL, 0, 0, NULL),
    (N'fact_sales', N'source_row_number', N'bigint', NULL, NULL, NULL, 0, 0, NULL),
    (N'fact_sales', N'invoice_no', N'nvarchar', 32, NULL, NULL, 0, 0, NULL),
    (N'fact_sales', N'invoice_datetime', N'datetime2', NULL, NULL, 3, 0, 0, NULL),
    (N'fact_sales', N'date_key', N'int', NULL, NULL, NULL, 0, 0, NULL),
    (N'fact_sales', N'customer_key', N'int', NULL, NULL, NULL, 0, 0, NULL),
    (N'fact_sales', N'product_key', N'int', NULL, NULL, NULL, 0, 0, NULL),
    (N'fact_sales', N'country_key', N'int', NULL, NULL, NULL, 0, 0, NULL),
    (N'fact_sales', N'quantity', N'int', NULL, NULL, NULL, 0, 0, NULL),
    (N'fact_sales', N'unit_price', N'decimal', NULL, 19, 4, 0, 0, NULL),
    (N'fact_sales', N'line_amount', N'decimal', NULL, 30, 4, 0, 0, NULL),
    (N'fact_sales', N'is_cancelled', N'bit', NULL, NULL, NULL, 0, 0, NULL),
    (N'fact_sales', N'batch_month', N'date', NULL, NULL, NULL, 0, 0, NULL),
    (N'fact_sales', N'loaded_at', N'datetime2', NULL, NULL, 6, 0, 0, NULL),
    (N'fact_sales', N'classification', N'nvarchar', 32, NULL, NULL, 0, 0, NULL),
    (N'fact_sales', N'quality_flags', N'nvarchar', 256, NULL, NULL, 0, 0, NULL),
    (N'fact_sales', N'MISSING_CUSTOMER', N'bit', NULL, NULL, NULL, 0, 0, NULL),
    (N'fact_sales', N'INVALID_CUSTOMER_ID', N'bit', NULL, NULL, NULL, 0, 0, NULL),
    (N'fact_sales', N'MISSING_DESCRIPTION', N'bit', NULL, NULL, NULL, 0, 0, NULL),
    (N'fact_sales', N'ZERO_PRICE', N'bit', NULL, NULL, NULL, 0, 0, NULL),
    (N'fact_sales', N'QUANTITY_SIGN_MISMATCH', N'bit', NULL, NULL, NULL, 0, 0, NULL),
    (N'fact_sales', N'NONSTANDARD_INVOICE', N'bit', NULL, NULL, NULL, 0, 0, NULL),
    (N'fact_sales', N'MISSING_COUNTRY', N'bit', NULL, NULL, NULL, 0, 0, NULL);

IF EXISTS
(
    SELECT table_name, column_name, type_name, max_chars, numeric_precision,
           fractional_scale, is_nullable, is_identity, required_collation
    FROM @expected
    EXCEPT
    SELECT tb.name, c.name, ty.name,
           CASE WHEN ty.name = N'nvarchar' THEN c.max_length / 2
                WHEN ty.name = N'char' THEN c.max_length ELSE NULL END,
           CASE WHEN ty.name = N'decimal' THEN c.precision ELSE NULL END,
           CASE WHEN ty.name IN (N'decimal', N'datetime2') THEN c.scale ELSE NULL END,
           c.is_nullable, c.is_identity,
           CASE WHEN (tb.name = N'dim_customer' AND c.name = N'customer_id')
                  OR (tb.name = N'dim_product' AND c.name = N'stock_code')
                  OR (tb.name = N'dim_country' AND c.name = N'country')
                  OR (tb.name = N'fact_sales' AND c.name = N'source_row_id')
                THEN c.collation_name ELSE NULL END
    FROM sys.columns AS c
    JOIN sys.tables AS tb ON tb.object_id = c.object_id
    JOIN sys.schemas AS sc ON sc.schema_id = tb.schema_id
    JOIN sys.types AS ty ON ty.user_type_id = c.user_type_id
    WHERE sc.name = N'dw'
      AND tb.name IN (N'dim_date', N'dim_customer', N'dim_product', N'dim_country', N'fact_sales')
)
OR EXISTS
(
    SELECT tb.name, c.name, ty.name,
           CASE WHEN ty.name = N'nvarchar' THEN c.max_length / 2
                WHEN ty.name = N'char' THEN c.max_length ELSE NULL END,
           CASE WHEN ty.name = N'decimal' THEN c.precision ELSE NULL END,
           CASE WHEN ty.name IN (N'decimal', N'datetime2') THEN c.scale ELSE NULL END,
           c.is_nullable, c.is_identity,
           CASE WHEN (tb.name = N'dim_customer' AND c.name = N'customer_id')
                  OR (tb.name = N'dim_product' AND c.name = N'stock_code')
                  OR (tb.name = N'dim_country' AND c.name = N'country')
                  OR (tb.name = N'fact_sales' AND c.name = N'source_row_id')
                THEN c.collation_name ELSE NULL END
    FROM sys.columns AS c
    JOIN sys.tables AS tb ON tb.object_id = c.object_id
    JOIN sys.schemas AS sc ON sc.schema_id = tb.schema_id
    JOIN sys.types AS ty ON ty.user_type_id = c.user_type_id
    WHERE sc.name = N'dw'
      AND tb.name IN (N'dim_date', N'dim_customer', N'dim_product', N'dim_country', N'fact_sales')
    EXCEPT
    SELECT table_name, column_name, type_name, max_chars, numeric_precision,
           fractional_scale, is_nullable, is_identity, required_collation
    FROM @expected
)
    THROW 52002, 'Existing dw columns differ from the Phase 5 contract; nothing was dropped.', 1;

-- Named constraints are required; an incompatible existing table is not repaired.
IF EXISTS
(
    SELECT required_name
    FROM (VALUES
        (N'PK_dw_dim_date'), (N'UQ_dw_dim_date_full_date'),
        (N'PK_dw_dim_customer'), (N'UQ_dw_dim_customer_customer_id'),
        (N'PK_dw_dim_product'), (N'UQ_dw_dim_product_stock_code'),
        (N'PK_dw_dim_country'), (N'UQ_dw_dim_country_country'),
        (N'PK_dw_fact_sales'), (N'UQ_dw_fact_sales_source_row_id')
    ) AS required_keys(required_name)
    EXCEPT
    SELECT kc.name
    FROM sys.key_constraints AS kc
    JOIN sys.indexes AS i
      ON i.object_id = kc.parent_object_id AND i.index_id = kc.unique_index_id
    WHERE kc.schema_id = SCHEMA_ID(N'dw')
      AND i.is_unique = 1 AND i.is_disabled = 0
)
    THROW 52003, 'A required dw primary/unique key is missing; nothing was changed automatically.', 1;

IF EXISTS
(
    SELECT required_name
    FROM (VALUES
        (N'CK_dw_dim_date_key'), (N'CK_dw_dim_customer_unknown'),
        (N'CK_dw_dim_country_unknown'),
        (N'CK_dw_fact_sales_source_row_number'),
        (N'CK_dw_fact_sales_classification'),
        (N'CK_dw_fact_sales_nonnegative_price')
    ) AS required_checks(required_name)
    EXCEPT
    SELECT name FROM sys.check_constraints
    WHERE schema_id = SCHEMA_ID(N'dw') AND is_disabled = 0 AND is_not_trusted = 0
)
    THROW 52005, 'A required trusted dw check constraint is missing.', 1;

IF EXISTS
(
    SELECT required_name
    FROM (VALUES
        (N'FK_dw_fact_sales_date'), (N'FK_dw_fact_sales_customer'),
        (N'FK_dw_fact_sales_product'), (N'FK_dw_fact_sales_country')
    ) AS required_fks(required_name)
    EXCEPT
    SELECT name FROM sys.foreign_keys
    WHERE schema_id = SCHEMA_ID(N'dw') AND is_disabled = 0 AND is_not_trusted = 0
)
    THROW 52004, 'A required trusted dw foreign key is missing; nothing was changed automatically.', 1;

SELECT sc.name AS schema_name, tb.name AS table_name
FROM sys.tables AS tb
JOIN sys.schemas AS sc ON sc.schema_id = tb.schema_id
WHERE sc.name = N'dw'
  AND tb.name IN (N'dim_date', N'dim_customer', N'dim_product', N'dim_country', N'fact_sales')
ORDER BY CASE tb.name WHEN N'dim_date' THEN 1 WHEN N'dim_customer' THEN 2
                      WHEN N'dim_product' THEN 3 WHEN N'dim_country' THEN 4 ELSE 5 END;

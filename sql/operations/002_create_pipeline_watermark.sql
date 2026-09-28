-- One control row for chronological historical monthly replay. No reset.
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF DB_NAME() <> N'RetailAnalytics'
    THROW 53001, 'Connect to RetailAnalytics before creating the watermark.', 1;
IF SCHEMA_ID(N'ops') IS NULL
    THROW 53002, 'Run 001_create_ops_schema.sql first.', 1;

IF OBJECT_ID(N'ops.pipeline_watermark', N'U') IS NULL
BEGIN
    CREATE TABLE ops.pipeline_watermark
    (
        pipeline_name NVARCHAR(100) NOT NULL
            CONSTRAINT PK_ops_pipeline_watermark PRIMARY KEY,
        last_processed_period DATE NULL,
        updated_at_utc DATETIME2(6) NOT NULL,
        CONSTRAINT CK_ops_pipeline_watermark_first_day
            CHECK (last_processed_period IS NULL OR DAY(last_processed_period) = 1)
    );
END;

-- Existing objects are inspected, not altered or dropped automatically.
DECLARE @expected TABLE
(
    column_name SYSNAME PRIMARY KEY,
    type_name SYSNAME NOT NULL,
    max_chars SMALLINT NULL,
    fractional_scale TINYINT NULL,
    is_nullable BIT NOT NULL
);
INSERT INTO @expected VALUES
    (N'pipeline_name', N'nvarchar', 100, NULL, 0),
    (N'last_processed_period', N'date', NULL, NULL, 1),
    (N'updated_at_utc', N'datetime2', NULL, 6, 0);

IF EXISTS
(
    SELECT column_name, type_name, max_chars, fractional_scale, is_nullable
    FROM @expected
    EXCEPT
    SELECT c.name, ty.name,
           CASE WHEN ty.name = N'nvarchar' THEN c.max_length / 2 ELSE NULL END,
           CASE WHEN ty.name = N'datetime2' THEN c.scale ELSE NULL END,
           c.is_nullable
    FROM sys.columns AS c
    JOIN sys.types AS ty ON ty.user_type_id = c.user_type_id
    WHERE c.object_id = OBJECT_ID(N'ops.pipeline_watermark', N'U')
)
OR EXISTS
(
    SELECT c.name, ty.name,
           CASE WHEN ty.name = N'nvarchar' THEN c.max_length / 2 ELSE NULL END,
           CASE WHEN ty.name = N'datetime2' THEN c.scale ELSE NULL END,
           c.is_nullable
    FROM sys.columns AS c
    JOIN sys.types AS ty ON ty.user_type_id = c.user_type_id
    WHERE c.object_id = OBJECT_ID(N'ops.pipeline_watermark', N'U')
    EXCEPT
    SELECT column_name, type_name, max_chars, fractional_scale, is_nullable
    FROM @expected
)
    THROW 53003, 'Existing ops.pipeline_watermark columns are incompatible.', 1;

IF NOT EXISTS
(
    SELECT 1 FROM sys.key_constraints
    WHERE parent_object_id = OBJECT_ID(N'ops.pipeline_watermark', N'U')
      AND name = N'PK_ops_pipeline_watermark' AND [type] = N'PK'
)
    THROW 53004, 'Expected watermark primary key is missing.', 1;
IF NOT EXISTS
(
    SELECT 1 FROM sys.check_constraints
    WHERE parent_object_id = OBJECT_ID(N'ops.pipeline_watermark', N'U')
      AND name = N'CK_ops_pipeline_watermark_first_day'
      AND is_disabled = 0 AND is_not_trusted = 0
)
    THROW 53005, 'Expected first-of-month check is missing or untrusted.', 1;

IF NOT EXISTS
(
    SELECT 1 FROM ops.pipeline_watermark
    WHERE pipeline_name = N'retail_monthly_replay'
)
    INSERT INTO ops.pipeline_watermark
        (pipeline_name, last_processed_period, updated_at_utc)
    VALUES (N'retail_monthly_replay', NULL, SYSUTCDATETIME());

SELECT pipeline_name, last_processed_period, updated_at_utc
FROM ops.pipeline_watermark
WHERE pipeline_name = N'retail_monthly_replay';

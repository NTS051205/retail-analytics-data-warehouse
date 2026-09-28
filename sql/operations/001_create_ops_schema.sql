-- Phase 6: non-destructive, rerunnable operational schema creation.
SET NOCOUNT ON;
IF DB_NAME() <> N'RetailAnalytics'
    THROW 53000, 'Connect to RetailAnalytics before creating ops.', 1;
IF SCHEMA_ID(N'ops') IS NULL
    EXEC(N'CREATE SCHEMA ops');
SELECT SCHEMA_ID(N'ops') AS ops_schema_id;

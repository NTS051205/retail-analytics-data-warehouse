-- Phase 4: execute in RetailAnalytics. Safe to rerun; never drops a schema.
SET NOCOUNT ON;

IF DB_NAME() <> N'RetailAnalytics'
    THROW 51000, 'Connect to RetailAnalytics before creating the stg schema.', 1;

IF SCHEMA_ID(N'stg') IS NULL
    EXEC(N'CREATE SCHEMA stg');

SELECT DB_NAME() AS database_name, SCHEMA_ID(N'stg') AS stg_schema_id;

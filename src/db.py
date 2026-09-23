"""Small, explicit SQL Server connection helper for Phase 4."""

from __future__ import annotations

import os
from dataclasses import dataclass

import pyodbc
from dotenv import load_dotenv

from .config import PROJECT_ROOT


@dataclass(frozen=True)
class SqlServerSettings:
    server: str
    database: str
    driver: str
    auth_mode: str
    trust_server_certificate: str
    username: str = ""
    password: str = ""

    @classmethod
    def from_environment(cls) -> "SqlServerSettings":
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        settings = cls(
            server=os.getenv("SQL_SERVER_HOST", "NGUYENSON").strip(),
            database=os.getenv("SQL_SERVER_DATABASE", "RetailAnalytics").strip(),
            driver=os.getenv("SQL_SERVER_DRIVER", "ODBC Driver 17 for SQL Server").strip(),
            auth_mode=os.getenv("SQL_SERVER_AUTH_MODE", "windows").strip().lower(),
            trust_server_certificate=os.getenv(
                "SQL_SERVER_TRUST_SERVER_CERTIFICATE", "yes"
            ).strip().lower(),
            username=os.getenv("SQL_SERVER_USERNAME", ""),
            password=os.getenv("SQL_SERVER_PASSWORD", ""),
        )
        if not settings.server or not settings.database or not settings.driver:
            raise ValueError("SQL_SERVER_HOST, DATABASE, and DRIVER must not be blank.")
        if settings.auth_mode not in {"windows", "sql"}:
            raise ValueError("SQL_SERVER_AUTH_MODE must be 'windows' or 'sql'.")
        if settings.trust_server_certificate not in {"yes", "no"}:
            raise ValueError("SQL_SERVER_TRUST_SERVER_CERTIFICATE must be 'yes' or 'no'.")
        if settings.auth_mode == "sql" and (not settings.username or not settings.password):
            raise ValueError("SQL authentication requires username and password in .env.")
        return settings

    def connection_string(self) -> str:
        def value(text: str) -> str:
            # ODBC braces keep semicolons in values from becoming new options.
            return "{" + text.replace("}", "}}") + "}"

        parts = [
            f"DRIVER={value(self.driver)}",
            f"SERVER={value(self.server)}",
            f"DATABASE={value(self.database)}",
            f"TrustServerCertificate={self.trust_server_certificate}",
        ]
        if self.auth_mode == "windows":
            parts.append("Trusted_Connection=yes")
        else:
            parts.extend(
                [f"UID={value(self.username)}", f"PWD={value(self.password)}"]
            )
        return ";".join(parts) + ";"


def connect(settings: SqlServerSettings, *, autocommit: bool = False) -> pyodbc.Connection:
    if settings.driver not in pyodbc.drivers():
        raise RuntimeError(
            f"ODBC driver {settings.driver!r} is not installed or visible to Python."
        )
    return pyodbc.connect(
        settings.connection_string(),
        timeout=10,
        autocommit=autocommit,
    )


def verify_connection(conn: pyodbc.Connection, settings: SqlServerSettings) -> dict[str, str]:
    """Confirm the actual SQL Server and database without displaying secrets."""
    row = conn.cursor().execute(
        "SELECT CONVERT(NVARCHAR(128), SERVERPROPERTY('MachineName')), "
        "DB_NAME(), CONVERT(NVARCHAR(128), SERVERPROPERTY('Edition')), "
        "CONVERT(NVARCHAR(128), SERVERPROPERTY('ProductVersion'))"
    ).fetchone()
    if row is None:
        raise RuntimeError("SQL Server did not return connection identity.")
    machine, database, edition, version = (str(item) for item in row)
    expected_machine = settings.server.split("\\", 1)[0].split(",", 1)[0]
    if machine.casefold() != expected_machine.casefold():
        raise RuntimeError(
            f"Connected to SQL machine {machine!r}, expected {expected_machine!r}."
        )
    if database.casefold() != settings.database.casefold():
        raise RuntimeError(
            f"Connected to database {database!r}, expected {settings.database!r}."
        )
    return {
        "server": machine,
        "database": database,
        "edition": edition,
        "product_version": version,
        "auth_mode": settings.auth_mode,
    }

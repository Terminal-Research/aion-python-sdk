"""Database utility functions."""

from __future__ import annotations

import uuid
from typing import Literal, Optional

import psycopg
from aion.shared.logging import get_logger

logger = get_logger()

__all__ = [
    "convert_pg_url",
    "verify_connection",
    "validate_permissions",
]


def convert_pg_url(
    url: Optional["str"] = None,
    driver: Literal["psycopg", "psycopg2", "asyncpg"] | None = None,
) -> str:
    """Convert PostgreSQL connection URL to the format required by specific drivers.

    This function handles URL conversion between different PostgreSQL driver formats:
    - psycopg: Uses postgresql+psycopg:// prefix (SQLAlchemy with psycopg3)
    - psycopg2: Uses postgresql+psycopg2:// prefix (SQLAlchemy with psycopg2)
    - asyncpg: Uses postgresql+asyncpg:// prefix (SQLAlchemy with asyncpg)
    - None: Standard postgresql:// format (default)

    Args:
        url: The PostgreSQL connection URL to convert.
        driver: Target driver format. If None, returns standard postgresql:// format.

    Returns:
        The URL formatted for the specified driver.

    Examples:
        >>> convert_pg_url("postgresql://user:pass@host/db", "psycopg")
        'postgresql+psycopg://user:pass@host/db'
        >>> convert_pg_url("postgresql+psycopg://user:pass@host/db", None)
        'postgresql://user:pass@host/db'
        >>> convert_pg_url("postgres://user:pass@host/db", "psycopg2")
        'postgresql+psycopg2://user:pass@host/db'
        >>> convert_pg_url("postgresql://user:pass@host/db", "asyncpg")
        'postgresql+asyncpg://user:pass@host/db'
    """
    if not url or "://" not in url:
        return url

    # Split protocol and rest of URL
    protocol, rest = url.split("://", 1)

    # Convert to target format
    if driver == "psycopg":
        return f"postgresql+psycopg://{rest}"
    elif driver == "psycopg2":
        return f"postgresql+psycopg2://{rest}"
    elif driver == "asyncpg":
        return f"postgresql+asyncpg://{rest}"
    else:
        # None uses standard format
        return f"postgresql://{rest}"


async def verify_connection(url: str) -> bool:
    """Attempt to connect to Postgres using ``psycopg`` async.

    Args:
        url: Connection URL.

    Returns:
        ``True`` if the connection succeeds, ``False`` otherwise.
    """
    try:
        async with await psycopg.AsyncConnection.connect(url) as conn:
            # Parse connection info for more helpful logging
            try:
                from urllib.parse import urlparse

                parsed = urlparse(url)
                host = parsed.hostname or ""
                port = f":{parsed.port}" if parsed.port else ""
                dbname = parsed.path.lstrip("/") if parsed.path else ""
                dbpart = f"/{dbname}" if dbname else ""
                details = f"{host}{port}{dbpart}" if (host or dbpart) else ""
            except Exception:
                details = ""
            if details:
                logger.info("Successfully connected to Postgres at %s", details)
            else:
                logger.info("Successfully connected to Postgres")
        return True
    except Exception as exc:  # pragma: no cover - connection failures
        logger.error("Could not connect to Postgres: %s", exc)
        return False


async def validate_permissions(url: str) -> dict:
    """Test database permissions for the current user.

    This function attempts various database operations to determine
    what permissions the current user has.

    Args:
        url: Connection URL.

    Returns:
        Dictionary with test results.
    """
    results = {
        "can_connect": False,
        "can_create_table": False,
        "can_create_schema": False,
        "user_info": None,
        "current_database": None,
        "error": None
    }

    try:
        # Test connection
        async with await psycopg.AsyncConnection.connect(url) as conn:
            results["can_connect"] = True

            # Get user info
            async with conn.cursor() as cur:
                await cur.execute("SELECT current_user, current_database(), session_user")
                user_info = await cur.fetchone()
                if user_info:
                    results["user_info"] = {
                        "current_user": user_info[0],
                        "current_database": user_info[1],
                        "session_user": user_info[2]
                    }
                    results["current_database"] = user_info[1]

            # Test table creation
            try:
                # Generate unique names to avoid conflicts with parallel executions
                unique_id = uuid.uuid4().hex[:8]
                test_table = f"_test_permissions_{unique_id}"

                async with conn.cursor() as cur:
                    # Test table creation in a transaction that we'll rollback
                    await cur.execute("BEGIN")
                    await cur.execute(f"CREATE TABLE {test_table} (id serial PRIMARY KEY)")
                    await cur.execute("ROLLBACK")
                    results["can_create_table"] = True
            except Exception as table_exc:
                results["can_create_table"] = False
                results["table_error"] = str(table_exc)

            # Test schema creation
            try:
                # Generate unique name to avoid conflicts with parallel executions
                unique_id = uuid.uuid4().hex[:8]
                test_schema = f"_test_schema_{unique_id}"

                async with conn.cursor() as cur:
                    # Test schema creation in a transaction that we'll rollback
                    await cur.execute("BEGIN")
                    await cur.execute(f"CREATE SCHEMA {test_schema}")
                    await cur.execute("ROLLBACK")
                    results["can_create_schema"] = True
            except Exception as schema_exc:
                results["can_create_schema"] = False
                results["schema_error"] = str(schema_exc)

    except Exception as exc:
        results["error"] = str(exc)

    return results

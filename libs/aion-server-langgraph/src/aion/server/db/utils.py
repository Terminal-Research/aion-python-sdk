"""Database utility functions."""

from __future__ import annotations

import psycopg
from aion.shared.logging import get_logger

logger = get_logger(__name__)


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
                async with conn.cursor() as cur:
                    # Create a test table
                    await cur.execute("CREATE TABLE IF NOT EXISTS _test_permissions (id serial PRIMARY KEY)")
                    await conn.commit()
                    results["can_create_table"] = True

                    # Clean up
                    await cur.execute("DROP TABLE _test_permissions")
                    await conn.commit()
            except Exception as table_exc:
                results["can_create_table"] = False
                results["table_error"] = str(table_exc)

            # Test schema creation
            try:
                async with conn.cursor() as cur:
                    # Create a test schema
                    await cur.execute("CREATE SCHEMA IF NOT EXISTS _test_schema")
                    await conn.commit()
                    results["can_create_schema"] = True

                    # Clean up
                    await cur.execute("DROP SCHEMA _test_schema")
                    await conn.commit()
            except Exception as schema_exc:
                results["can_create_schema"] = False
                results["schema_error"] = str(schema_exc)

    except Exception as exc:
        results["error"] = str(exc)

    return results

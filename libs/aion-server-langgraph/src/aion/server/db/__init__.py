"""PostgreSQL database utilities for the Aion server."""

from __future__ import annotations

import os
from dataclasses import dataclass

import logging
import psycopg

# Default namespace for database objects
AION_DB_NAMESPACE = "aion"


@dataclass
class DatabaseConfig:
    """Configuration for connecting to Postgres."""

    url: str


def get_config() -> DatabaseConfig | None:
    """Return database configuration from the ``POSTGRES_URL`` environment.

    Returns ``None`` if the variable is not set.
    """
    url = os.getenv("POSTGRES_URL")
    if not url:
        return None
    return DatabaseConfig(url=url)


__all__ = [
    "AION_DB_NAMESPACE",
    "DatabaseConfig",
    "get_config",
    "sqlalchemy_url",
    "test_connection",
    "test_permissions",
]

# Module logger
logger = logging.getLogger(__name__)


def test_connection(url: str) -> bool:
    """Attempt to connect to Postgres using ``psycopg``.

    Args:
        url: Connection URL.

    Returns:
        ``True`` if the connection succeeds, ``False`` otherwise.
    """
    try:
        conn = psycopg.connect(url)
        conn.close()
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


def sqlalchemy_url(url: str) -> str:
    """Return a SQLAlchemy connection URL using the ``psycopg`` driver.

    SQLAlchemy requires the ``postgresql+psycopg://`` protocol when using
    ``psycopg`` version 3. This helper ensures the correct prefix is used
    without forcing callers to specify it in their environment variable.

    Args:
        url: The configured connection URL.

    Returns:
        The URL formatted for SQLAlchemy.
    """

    prefix = "postgresql+psycopg://"
    if url.startswith(prefix) or not url:
        return url
    if url.startswith("postgresql://"):
        return prefix + url[len("postgresql://") :]
    if url.startswith("postgres://"):
        return prefix + url[len("postgres://") :]
    return url


def test_permissions(url: str) -> dict:
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
        conn = psycopg.connect(url)
        results["can_connect"] = True
        
        # Get user info
        with conn.cursor() as cur:
            cur.execute("SELECT current_user, current_database(), session_user")
            user_info = cur.fetchone()
            if user_info:
                results["user_info"] = {
                    "current_user": user_info[0],
                    "current_database": user_info[1],
                    "session_user": user_info[2]
                }
                results["current_database"] = user_info[1]
        
        # Test table creation
        try:
            with conn.cursor() as cur:
                # Create a test table
                cur.execute("CREATE TABLE IF NOT EXISTS _test_permissions (id serial PRIMARY KEY)")
                conn.commit()
                results["can_create_table"] = True
                
                # Clean up
                cur.execute("DROP TABLE _test_permissions")
                conn.commit()
        except Exception as table_exc:
            results["can_create_table"] = False
            results["table_error"] = str(table_exc)
            
        # Test schema creation
        try:
            with conn.cursor() as cur:
                # Create a test schema
                cur.execute("CREATE SCHEMA IF NOT EXISTS _test_schema")
                conn.commit()
                results["can_create_schema"] = True
                
                # Clean up
                cur.execute("DROP SCHEMA _test_schema")
                conn.commit()
        except Exception as schema_exc:
            results["can_create_schema"] = False
            results["schema_error"] = str(schema_exc)
        
        conn.close()
        
    except Exception as exc:
        results["error"] = str(exc)
        
    return results

"""Database utility functions."""

from __future__ import annotations

import logging
import psycopg

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


def create_tables(url: str) -> bool:
    """Create all necessary database tables for the application.
    
    This function creates the tables needed for the application directly using
    SQLAlchemy, bypassing Alembic migrations. It also creates the alembic_version
    table and inserts the latest migration version to prevent future migration attempts.
    
    Args:
        url: Connection URL string.
        
    Returns:
        bool: True if tables were created successfully, False otherwise.
    """
    import logging
    from sqlalchemy import create_engine, MetaData, Table, Column, String, JSON, DateTime, text
    from sqlalchemy.dialects.postgresql import UUID
    from sqlalchemy.sql import func
    
    logger = logging.getLogger(__name__)
    
    try:
        # Format the URL for SQLAlchemy
        sa_url = sqlalchemy_url(url)
        logger.info(f"Creating tables in database: {url}")
        
        # Create engine and metadata
        engine = create_engine(sa_url)
        metadata = MetaData()
        
        # Create threads table
        logger.info("Creating threads table")
        threads = Table(
            "threads", metadata,
            Column("id", UUID(), primary_key=True),
            Column("context_id", String(), unique=True, nullable=False),
            Column("artifacts", JSON(), nullable=False),
            Column(
                "created_at",
                DateTime(timezone=True),
                nullable=False,
                server_default=func.now(),
            ),
            Column(
                "updated_at",
                DateTime(timezone=True),
                nullable=False,
                server_default=func.now(),
            ),
        )
        
        # Create tasks table
        logger.info("Creating tasks table")
        tasks = Table(
            "tasks", metadata,
            Column("id", UUID(), primary_key=True),
            Column("context_id", String(), unique=True, nullable=False),
            Column("task", JSON(), nullable=False),
            Column(
                "created_at",
                DateTime(timezone=True),
                nullable=False,
                server_default=func.now(),
            ),
            Column(
                "updated_at",
                DateTime(timezone=True),
                nullable=False,
                server_default=func.now(),
            ),
        )
        
        # Create alembic_version table to track migrations
        logger.info("Creating alembic_version table")
        alembic_version = Table(
            "alembic_version", metadata,
            Column("version_num", String(32), primary_key=True),
        )
        
        # Create all tables
        metadata.create_all(engine)
        
        # Insert the latest migration version
        logger.info("Setting alembic_version to latest migration")
        with engine.connect() as conn:
            conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('002') ON CONFLICT (version_num) DO NOTHING"))
            conn.commit()
            
        logger.info("All tables created successfully")
        return True
    except Exception as e:
        logger.error(f"Error creating tables: {e}", exc_info=True)
        return False

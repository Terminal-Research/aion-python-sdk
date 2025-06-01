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

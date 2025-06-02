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

# Import utility functions from utils module
from .utils import test_connection, sqlalchemy_url, test_permissions, create_tables

__all__ = [
    "AION_DB_NAMESPACE",
    "DatabaseConfig",
    "get_config",
    "sqlalchemy_url",
    "test_connection",
    "test_permissions",
    "create_tables",
]

# Module logger
logger = logging.getLogger(__name__)
# These functions are now imported from utils.py

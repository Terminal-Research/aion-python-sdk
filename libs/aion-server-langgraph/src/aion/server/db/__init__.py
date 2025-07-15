"""PostgreSQL database utilities for the Aion server."""

from __future__ import annotations

# Default namespace for database objects
AION_DB_NAMESPACE = "aion"


# Import utility functions from utils module
from .utils import (
    verify_connection,
    sqlalchemy_url,
    psycopg_url,
    test_permissions,
    get_config,
    DatabaseConfig,
)

from .manager import db_manager

__all__ = [
    "AION_DB_NAMESPACE",
    "DatabaseConfig",
    "get_config",
    "sqlalchemy_url",
    "psycopg_url",
    "verify_connection",
    "test_permissions",
    "db_manager",
]

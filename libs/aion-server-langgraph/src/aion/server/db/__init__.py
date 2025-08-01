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
)

from .manager import db_manager

from .models import TaskRecordModel

__all__ = [
    "AION_DB_NAMESPACE",
    "sqlalchemy_url",
    "psycopg_url",
    "verify_connection",
    "test_permissions",
    "db_manager",

    # db models
    "TaskRecordModel",
]

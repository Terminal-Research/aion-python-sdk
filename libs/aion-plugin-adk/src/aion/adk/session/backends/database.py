"""Database session service backend.

This module provides a database-backed session storage backend using
Google ADK's DatabaseSessionService with PostgreSQL.
"""

from typing import Optional

from aion.shared.db import DbManagerProtocol
from aion.shared.logging import get_logger
from aion.shared.utils.db import convert_pg_url
from google.adk.sessions import DatabaseSessionService

from .base import SessionServiceBackend

logger = get_logger()


class DatabaseBackend(SessionServiceBackend):
    """Database session service backend.

    This backend uses ADK's DatabaseSessionService for persistent session storage
    in PostgreSQL database.

    Use this backend for:
    - Production environments
    - Multi-instance deployments
    - When session persistence is required

    Attributes:
        _db_manager: Database manager for connection management
    """

    def __init__(self, db_manager: DbManagerProtocol):
        """Initialize database backend.

        Args:
            db_manager: Database manager instance for connection management
        """
        self._db_manager = db_manager

    def create(self) -> Optional[DatabaseSessionService]:
        """Create database-backed session service instance.

        Returns:
            DatabaseSessionService: ADK's database session service, or None if creation fails

        Raises:
            Exception: If database connection or service creation fails
        """
        try:
            if not self._db_manager or not self._db_manager.is_initialized:
                logger.warning("Database manager not initialized")
                return None

            # Get DSN from database manager
            db_url = self._db_manager.get_dsn()
            if not db_url:
                logger.warning("Database URL not available")
                return None

            # Convert PostgreSQL URL to psycopg format
            psycopg_url = convert_pg_url(db_url, driver="psycopg")

            session_service = DatabaseSessionService(db_url=psycopg_url)
            return session_service

        except Exception as ex:
            logger.error(f"Failed to create DatabaseSessionService: {ex}")
            return None

    def is_available(self) -> bool:
        """Check if database backend is available.

        Returns:
            bool: True if database manager is initialized and ready
        """
        return (
            self._db_manager is not None
            and self._db_manager.is_initialized
        )


__all__ = ["DatabaseBackend"]

"""Session service factory for ADK plugin.

This module provides the SessionServiceFactory for creating session service
instances by selecting the appropriate storage backend.
"""

from typing import Optional

from aion.shared.db import DbManagerProtocol
from aion.shared.logging import get_logger
from google.adk.sessions import BaseSessionService, InMemorySessionService
from google.adk.sessions.database_session_service import DatabaseSessionService

from .backends import DatabaseBackend, MemoryBackend

logger = get_logger()


class SessionServiceFactory:
    """Factory for creating ADK session service instances.

    Selects the appropriate backend based on database availability and returns
    a ready-to-use session service.
    """

    @classmethod
    def create(cls, db_manager: Optional[DbManagerProtocol] = None) -> BaseSessionService:
        """Create session service using the most appropriate backend.

        Args:
            db_manager: Optional database manager. If provided and initialized,
                        DatabaseSessionService is used; otherwise falls back to
                        InMemorySessionService.

        Returns:
            BaseSessionService: A session service instance
        """
        service = None
        if db_manager:
            service = cls._create_database(db_manager)

        if not service:
            service = cls._create_memory()

        logger.info(f"Initialized {type(service).__name__}")
        return service

    @staticmethod
    def _create_database(db_manager: DbManagerProtocol) -> Optional[DatabaseSessionService]:
        """Attempt to create a DatabaseSessionService.

        Returns None if the backend is unavailable or service creation fails.
        """
        backend = DatabaseBackend(db_manager)
        if not backend.is_available():
            logger.warning("Database backend unavailable")
            return None

        service = backend.create()
        if service is None:
            logger.warning("Failed to create DatabaseSessionService")

        return service

    @staticmethod
    def _create_memory() -> InMemorySessionService:
        """Create an InMemorySessionService as the default fallback."""
        return MemoryBackend().create()


__all__ = ["SessionServiceFactory"]

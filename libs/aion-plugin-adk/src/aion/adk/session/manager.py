"""ADK-specific session service management.

This module provides the SessionServiceManager for creating and managing
session service instances for different storage backends (in-memory or database-backed).

The manager provides dependency injection for database managers and automatic
fallback to in-memory storage when database is not available.
"""

from typing import Any, Optional

from aion.shared.db import DbManagerProtocol
from aion.shared.logging import get_logger

from .backends import SessionServiceBackendFactory

logger = get_logger()


class SessionServiceManager:
    """ADK session service manager with database dependency injection.

    This manager creates session service instances based on availability of
    database manager. If a database manager is provided and initialized,
    it creates a DatabaseSessionService. Otherwise, it falls back to
    InMemorySessionService.

    Example:
        # With database
        manager = SessionServiceManager(db_manager=db_manager)
        service = manager.create_session_service()  # Returns DatabaseSessionService

        # Without database
        manager = SessionServiceManager()
        service = manager.create_session_service()  # Returns InMemorySessionService
    """

    def __init__(self, db_manager: Optional[DbManagerProtocol] = None):
        """Initialize manager with optional database manager.

        Args:
            db_manager: Database manager instance for DatabaseSessionService support.
                       If None, will use InMemorySessionService.
        """
        self._db_manager = db_manager

    def create_session_service(self) -> Any:
        """Create appropriate session service based on database availability.

        Returns:
            InMemorySessionService or DatabaseSessionService depending on db_manager availability

        Logic:
            - If db_manager is provided and initialized: create DatabaseSessionService
            - Otherwise: fallback to InMemorySessionService
        """
        # Use factory to create appropriate backend
        backend = SessionServiceBackendFactory.create_backend(self._db_manager)

        # Create session service from backend
        session_service = backend.create()

        if session_service is None:
            logger.warning("Backend creation failed, falling back to memory backend")
            from .backends import MemoryBackend
            session_service = MemoryBackend().create()
            backend_name = "MemoryBackend"
        else:
            backend_name = type(backend).__name__

        # Single informative log about which backend was used
        backend_type = backend_name.replace("Backend", "").lower()
        logger.info(f"Initialized {backend_type} session service")

        return session_service


__all__ = ["SessionServiceManager"]

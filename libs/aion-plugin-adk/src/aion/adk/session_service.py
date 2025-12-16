"""ADK-specific session service management.

This module provides the ADKSessionServiceAdapter for creating session service
instances for different storage backends (in-memory or database-backed).

The adapter follows the same pattern as LangGraphCheckpointerAdapter, providing
dependency injection for database managers and automatic fallback to in-memory
storage when database is not available.
"""

from abc import ABC, abstractmethod
from typing import Any, Optional

from aion.shared.db import DbManagerProtocol
from aion.shared.logging import get_logger
from google.adk.sessions import InMemorySessionService, DatabaseSessionService

from aion.shared.utils.db import convert_pg_url

logger = get_logger()


class SessionServiceAdapter(ABC):
    """Abstract base for ADK session service management.

    Subclasses must implement factory methods for creating session service instances.
    The SessionServiceAdapter is responsible for:
    - Creating backend-specific session service instances
    - Managing database dependencies
    - Providing fallback to in-memory storage
    """

    @abstractmethod
    def create_session_service(self) -> Any:
        """Create a backend-specific session service instance.

        Returns:
            Any: A session service instance ready for use (InMemorySessionService or DatabaseSessionService)

        Raises:
            ValueError: If configuration is invalid
        """
        pass


class ADKSessionServiceAdapter(SessionServiceAdapter):
    """ADK session service adapter with database dependency injection.

    This adapter creates session service instances based on availability of
    database manager. If a database manager is provided and initialized,
    it creates a DatabaseSessionService. Otherwise, it falls back to
    InMemorySessionService.

    Example:
        # With database
        adapter = ADKSessionServiceAdapter(db_manager=db_manager)
        service = adapter.create_session_service()  # Returns DatabaseSessionService

        # Without database
        adapter = ADKSessionServiceAdapter()
        service = adapter.create_session_service()  # Returns InMemorySessionService
    """

    def __init__(self, db_manager: Optional[DbManagerProtocol] = None):
        """Initialize adapter with optional database manager.

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
        logger.debug("Creating ADK session service")

        if not self._db_manager:
            logger.info(
                "Database manager not provided, using InMemorySessionService"
            )
            return self._create_memory_session_service()

        if not self._db_manager.is_initialized:
            logger.warning(
                "Database manager not initialized, falling back to InMemorySessionService"
            )
            return self._create_memory_session_service()

        # Try to create database session service
        db_session_service = self._create_database_session_service()
        if db_session_service is None:
            logger.warning(
                "DatabaseSessionService creation failed, falling back to InMemorySessionService"
            )
            return self._create_memory_session_service()

        logger.info("Created DatabaseSessionService successfully")
        return db_session_service

    @staticmethod
    def _create_memory_session_service() -> InMemorySessionService:
        """Create in-memory session service.

        Returns:
            InMemorySessionService instance
        """
        return InMemorySessionService()

    def _create_database_session_service(self) -> Optional[DatabaseSessionService]:
        """Create database-backed session service using injected database manager.

        Returns:
            DatabaseSessionService instance or None if creation fails
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

            # Create DatabaseSessionService with the database URL
            session_service = DatabaseSessionService(db_url=convert_pg_url(db_url, driver="psycopg"))
            return session_service

        except Exception as ex:
            logger.error(f"Failed to create DatabaseSessionService: {ex}")
            return None

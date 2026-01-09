"""Base interface for session service backends.

This module defines the abstract base for different session storage backends.
All session service backends must inherit from SessionServiceBackend.
"""

from abc import ABC, abstractmethod
from typing import Any, Optional

from aion.shared.db import DbManagerProtocol


class SessionServiceBackend(ABC):
    """Abstract base for session service backends.

    Subclasses must implement the create method to return a concrete
    session service implementation (e.g., InMemorySessionService, DatabaseSessionService).
    """

    @abstractmethod
    def create(self) -> Any:
        """Create and return a session service instance.

        Returns:
            Any: A session service instance ready for use

        Raises:
            Exception: If session service creation fails
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this backend is available for use.

        Returns:
            bool: True if backend can be used, False otherwise
        """
        pass


class SessionServiceBackendFactory:
    """Factory for creating appropriate session service backends.

    This factory determines which backend to use based on available resources
    and configuration.
    """

    @staticmethod
    def create_backend(db_manager: Optional[DbManagerProtocol] = None) -> SessionServiceBackend:
        """Create appropriate backend based on available resources.

        Args:
            db_manager: Optional database manager for database-backed sessions

        Returns:
            SessionServiceBackend: The most appropriate backend instance
        """
        from .database import DatabaseBackend
        from .memory import MemoryBackend

        # Try database backend first if db_manager is provided
        if db_manager:
            db_backend = DatabaseBackend(db_manager)
            if db_backend.is_available():
                return db_backend

        # Fallback to memory backend
        return MemoryBackend()


__all__ = ["SessionServiceBackend", "SessionServiceBackendFactory"]

"""Database manager protocol for dependency injection.

This module defines the abstract interface for database connection management.
Concrete implementations are provided by packages that need database functionality
"""

from abc import ABC, abstractmethod
from typing import Any


class DbManagerProtocol(ABC):
    """Abstract interface for database connection management.

    This protocol defines the contract that any database manager implementation
    must satisfy. It allows plugins and other components to depend on the
    abstraction rather than concrete implementations, following the Dependency
    Inversion Principle.
    """

    @property
    @abstractmethod
    def is_initialized(self) -> bool:
        """Check if database manager is initialized and ready.

        Returns:
            bool: True if initialized and connection pool is ready, False otherwise
        """
        pass

    @abstractmethod
    def get_pool(self) -> Any:
        """Get the database connection pool.

        Returns:
            Connection pool instance (implementation-specific, e.g.,
            AsyncConnectionPool for psycopg, or other pool implementations)

        Raises:
            RuntimeError: If manager is not initialized
        """
        pass

    @abstractmethod
    async def initialize(self, dsn: str) -> None:
        """Initialize database connections.

        Args:
            dsn: Database connection string (format depends on implementation)

        Raises:
            Exception: If initialization fails
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close all database connections and cleanup resources.

        This should be called during application shutdown to ensure
        proper cleanup of database connections.
        """
        pass

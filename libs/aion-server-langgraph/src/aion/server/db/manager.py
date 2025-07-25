import logging
from typing import Optional

from psycopg_pool import AsyncConnectionPool

from aion.server.core.metaclasses import Singleton

logger = logging.getLogger(__name__)


class DbManager(metaclass=Singleton):
    """Manages PostgreSQL connection pool for the application."""

    def __init__(self):
        """Initialize the database manager with no active pool."""
        self._pool: Optional[AsyncConnectionPool] = None

    @property
    def is_initialized(self) -> bool:
        """Check if the database pool is initialized and open."""
        return self._pool is not None and not self._pool.closed

    async def initialize(self, dsn: str) -> None:
        """Initialize the database connection pool.

        Args:
            dsn: PostgreSQL connection string.
        """
        if self.is_initialized:
            logger.warning("Database already initialized")
            return

        self._pool = AsyncConnectionPool(
            dsn,
            min_size=2,
            max_size=10,
            max_idle=300,
            max_lifetime=3600,
            timeout=30,
            max_waiting=20,
            open=False)

        await self._pool.open()
        await self._pool.wait()
        logger.info(f"Pool created with {self._pool.get_stats()}")

    def get_pool(self) -> AsyncConnectionPool:
        """Get the active connection pool.

        Returns:
            Active PostgreSQL connection pool.

        Raises:
            RuntimeError: If pool is not initialized.
        """
        if not self._pool:
            logger.error("No database connection pool initialized.")
            raise RuntimeError("Pool not initialized")
        return self._pool

    async def close(self) -> None:
        """Close the database connection pool and cleanup resources."""
        if not self._pool:
            return

        logger.info('Closing database connection pool')
        await self._pool.close()
        self._pool = None
        logger.info('Database connection pool closed')


db_manager = DbManager()

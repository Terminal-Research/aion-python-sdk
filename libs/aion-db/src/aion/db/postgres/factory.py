"""Database factory for managing database initialization and cleanup.

This module provides DbFactory which handles database connection setup,
migrations, and resource cleanup.
"""

import logging
from aion.db.settings import db_settings

from aion.db.postgres.utils import verify_connection
from aion.db.postgres.manager import DbManager
from aion.db.postgres.migrations import upgrade_to_head

logger = logging.getLogger(__name__)


class DbFactory:
    """Factory for database initialization and management.

    Handles:
    - Database connection verification
    - DbManager initialization
    - Database migrations
    - Resource cleanup
    """

    def __init__(self, db_manager: DbManager):
        """Initialize the database factory.

        Args:
            db_manager: Database manager instance to initialize
        """
        self.db_manager = db_manager

    async def initialize(self) -> bool:
        """Initialize database connection and run migrations.

        Returns:
            bool: True if PostgreSQL is ready, False only when ``POSTGRES_URL``
                is absent - the explicit single-process configuration.

        Raises:
            RuntimeError: ``POSTGRES_URL`` is set but the connection cannot be
                verified, the manager fails to initialize, or migrations fail.
                A configured PostgreSQL that turns out unreachable must stop
                startup rather than silently degrade to an in-memory store
                with no ownership enforcement across pods.
        """
        pg_url = db_settings.pg_url
        if not pg_url:
            logger.debug("POSTGRES_URL environment variable not set, using in-memory data store")
            return False

        # Verify connection
        is_connection_verified = await verify_connection(pg_url)
        if not is_connection_verified:
            logger.error("POSTGRES_URL is set but the connection cannot be verified")
            await self.cleanup()
            raise RuntimeError(
                "Configured PostgreSQL is unreachable; refusing to fall back "
                "to an in-memory store"
            )

        # Initialize database manager
        try:
            await self.db_manager.initialize(pg_url)
        except Exception as exc:
            logger.error("Failed to initialize database", exc_info=exc)
            await self.cleanup()
            raise RuntimeError(
                "Configured PostgreSQL failed to initialize; refusing to "
                "fall back to an in-memory store"
            ) from exc

        # Run migrations
        try:
            await upgrade_to_head()
            logger.info("Database migrations completed successfully")
            return True
        except Exception as exc:
            logger.error("Migration failed: %s", exc, exc_info=True)
            await self.cleanup()
            raise RuntimeError(
                "Database migration failed; refusing to fall back to an "
                "in-memory store"
            ) from exc

    async def cleanup(self) -> None:
        """Close database connections if initialized."""
        if not self.db_manager.is_initialized:
            return

        try:
            await self.db_manager.close()
            logger.info("Database connections closed")
        except Exception as exc:
            logger.error("Error closing database", exc_info=exc)

    @property
    def is_initialized(self) -> bool:
        """Check if database is initialized.

        Returns:
            bool: True if db_manager is initialized
        """
        return self.db_manager.is_initialized


__all__ = ["DbFactory"]

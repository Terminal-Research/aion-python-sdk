"""Database factory for managing database initialization and cleanup.

This module provides DbFactory which handles database connection setup,
migrations, and resource cleanup.
"""

from aion.shared.logging import get_logger
from aion.shared.settings import db_settings

from aion.server.db import verify_connection
from aion.server.db.manager import DbManager
from aion.server.db.migrations import upgrade_to_head

logger = get_logger()


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
            bool: True if initialization successful, False otherwise
        """
        pg_url = db_settings.pg_url
        if not pg_url:
            logger.debug("POSTGRES_URL environment variable not set, using in-memory data store")
            return False

        # Verify connection
        is_connection_verified = await verify_connection(pg_url)
        if not is_connection_verified:
            logger.warning("Cannot verify postgres connection")
            return False

        # Initialize database manager
        try:
            await self.db_manager.initialize(pg_url)
        except Exception as exc:
            logger.error("Failed to initialize database", exc_info=exc)
            await self.cleanup()
            return False

        # Run migrations
        try:
            await upgrade_to_head()
            logger.info("Database migrations completed successfully")
            return True
        except Exception as exc:
            logger.error("Migration failed: %s", exc, exc_info=True)
            await self.cleanup()
            return False

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

"""LangGraph plugin for AION framework.

This module provides the LangGraph plugin which integrates the LangGraph
agent framework into the AION system, handling setup, database migrations,
and adapter initialization.
"""

from typing import Any, Optional, TYPE_CHECKING

from aion.shared.db import DbManagerProtocol
from aion.shared.logging.base import AionLogger
from aion.shared.plugins import AgentPluginProtocol

from .agent import LangGraphAdapter

if TYPE_CHECKING:
    from aion.shared.logging.base import AionLogger


class LangGraphPlugin(AgentPluginProtocol):
    """LangGraph framework plugin.

    This plugin handles LangGraph-specific infrastructure setup including:
    - PostgreSQL checkpointer table creation
    - Adapter initialization with dependency injection
    - Health checks for database connectivity
    """

    NAME = "langgraph"

    def __init__(self):
        """Initialize the LangGraph plugin."""
        self._db_manager: Optional[DbManagerProtocol] = None
        self._adapter: Optional[LangGraphAdapter] = None
        self._logger: Optional[AionLogger] = None

    @property
    def logger(self) -> AionLogger:
        if not self._logger:
            from aion.shared.logging import get_logger
            self._logger = get_logger(__name__)
        return self._logger

    def name(self) -> str:
        """Get plugin name.

        Returns:
            str: Plugin identifier
        """
        return self.NAME

    async def setup(self, **deps: Any) -> None:
        """Setup the LangGraph plugin.

        Initializes the plugin by:
        1. Storing the database manager
        2. Running checkpointer table migrations if database is available
        3. Creating the LangGraph adapter with injected dependencies

        Args:
            **deps: Dependencies including optional db_manager
        """
        db_manager = deps.get("db_manager")
        self._db_manager = db_manager

        # Setup checkpointer tables if database is available
        if db_manager and db_manager.is_initialized:
            self.logger.info("Setting up LangGraph checkpointer tables")
            await self._setup_checkpointer_tables(db_manager)

        # Initialize adapter with dependencies
        self._adapter = LangGraphAdapter(db_manager=db_manager)
        self.logger.debug(f"LangGraph adapter initialized")

    async def teardown(self) -> None:
        """Cleanup plugin resources.

        Currently no cleanup is needed as the adapter doesn't hold
        resources that need explicit cleanup.
        """
        self._adapter = None
        self._db_manager = None

    def get_adapter(self) -> LangGraphAdapter:
        """Get the LangGraph adapter instance.

        Returns:
            LangGraphAdapter: The initialized adapter

        Raises:
            RuntimeError: If called before setup()
        """
        if not self._adapter:
            raise RuntimeError(
                f"{self.name()} plugin not initialized. Call setup() first."
            )
        return self._adapter

    async def health_check(self) -> bool:
        """Check plugin health.

        Verifies that:
        - Adapter is initialized
        - Database manager is initialized (if provided)

        Returns:
            bool: True if plugin is healthy
        """
        if not self._adapter:
            return False

        # If database was provided, check it's still initialized
        if self._db_manager:
            return self._db_manager.is_initialized

        return True

    async def _setup_checkpointer_tables(self, db_manager: DbManagerProtocol) -> None:
        """Setup LangGraph checkpointer tables in the database.

        Creates the necessary tables for PostgreSQL checkpointer if they
        don't already exist. Uses LangGraph's built-in setup mechanism.

        Args:
            db_manager: Database manager instance

        Raises:
            ImportError: If langgraph-checkpoint-postgres is not available
        """
        try:
            self.logger.debug("Attempting to setup LangGraph checkpointer tables")

            # Import here to avoid hard dependency
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            # Get connection pool and convert to psycopg format
            pool = db_manager.get_pool()

            # Create checkpointer and setup tables
            # AsyncPostgresSaver accepts the pool directly
            checkpointer = AsyncPostgresSaver(conn=pool)
            await checkpointer.setup()

            self.logger.info("LangGraph checkpointer tables created successfully")

        except ImportError:
            self.logger.warning(
                "langgraph-checkpoint-postgres not available - "
                "PostgreSQL checkpointer will not be available. "
                "Install with: pip install langgraph-checkpoint-postgres"
            )
        except Exception as e:
            self.logger.error(f"Failed to setup checkpointer tables: {e}", exc_info=True)
            raise


# Export for easy import
__all__ = ["LangGraphPlugin"]

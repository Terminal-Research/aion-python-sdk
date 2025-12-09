"""LangGraph plugin for AION framework."""

from aion.shared.db import DbManagerProtocol
from aion.shared.logging import AionLogger, get_logger
from aion.shared.plugins import AgentPluginProtocol
from typing import Any, Optional

from .agent import LangGraphAdapter


class LangGraphPlugin(AgentPluginProtocol):
    """LangGraph framework plugin."""

    NAME = "langgraph"

    def __init__(self):
        self._db_manager: Optional[DbManagerProtocol] = None
        self._adapter: Optional[LangGraphAdapter] = None
        self._logger: Optional[AionLogger] = None

    @property
    def logger(self) -> AionLogger:
        if not self._logger:
            self._logger = get_logger()
        return self._logger

    def name(self) -> str:
        return self.NAME

    async def setup(self, db_manager: DbManagerProtocol, **deps: Any) -> None:
        self._db_manager = db_manager

        if db_manager and db_manager.is_initialized:
            self.logger.info("Setting up LangGraph checkpointer tables")
            await self._setup_checkpointer_tables(db_manager)

        self._adapter = LangGraphAdapter(db_manager=db_manager)

    async def teardown(self) -> None:
        """Cleanup plugin resources.

        Currently no cleanup is needed as the adapter doesn't hold
        resources that need explicit cleanup.
        """
        self._adapter = None
        self._db_manager = None

    def get_adapter(self) -> LangGraphAdapter:
        if not self._adapter:
            raise RuntimeError(
                f"{self.name()} plugin not initialized. Call setup() first."
            )
        return self._adapter

    async def health_check(self) -> bool:
        return self._adapter is not None

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

            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            pool = db_manager.get_pool()
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


__all__ = ["LangGraphPlugin"]

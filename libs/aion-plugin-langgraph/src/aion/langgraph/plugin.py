"""LangGraph plugin for AION framework."""

from typing import Any, Optional, override

from aion.shared.agent import AionAgent
from aion.shared.db import DbManagerProtocol
from aion.shared.logging import AionLogger, get_logger
from aion.shared.plugins import AgentPluginProtocol
from fastapi import FastAPI

from .adapter import LangGraphAdapter


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

    @override
    def name(self) -> str:
        return self.NAME

    @override
    async def initialize(self, db_manager: DbManagerProtocol, **deps: Any) -> None:
        self._db_manager = db_manager

        if db_manager and db_manager.is_initialized:
            self.logger.info("Setting up LangGraph checkpointer tables")
            await self._setup_checkpointer_tables(db_manager)

        self._adapter = LangGraphAdapter(db_manager=db_manager)

    @override
    async def teardown(self) -> None:
        """Cleanup plugin resources.

        Currently no cleanup is needed as the adapter doesn't hold
        resources that need explicit cleanup.
        """
        self._adapter = None
        self._db_manager = None

    @override
    def get_adapter(self) -> LangGraphAdapter:
        if not self._adapter:
            raise RuntimeError(
                f"{self.name()} plugin not initialized. Call initialize() first."
            )
        return self._adapter

    @override
    async def health_check(self) -> bool:
        return self._adapter is not None

    async def _setup_checkpointer_tables(self, db_manager: DbManagerProtocol) -> None:
        """Setup LangGraph checkpointer tables in the database.

        Creates the necessary tables for PostgreSQL checkpointer if they
        don't already exist. Uses LangGraph's built-in setup mechanism.

        This method is idempotent - it can be safely called multiple times.
        LangGraph's setup() uses a migration system that will apply only
        new migrations, allowing for future schema updates.

        When multiple agents start simultaneously, they may race to create
        the same database objects. UniqueViolation errors are expected and
        ignored in this scenario.

        Args:
            db_manager: Database manager instance

        Raises:
            ImportError: If langgraph-checkpoint-postgres is not available
        """
        try:
            self.logger.debug("Attempting to setup LangGraph checkpointer tables")

            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            import psycopg.errors

            pool = db_manager.get_pool()

            # Use autocommit mode for CREATE INDEX CONCURRENTLY
            async with pool.connection() as conn:
                await conn.set_autocommit(True)
                checkpointer = AsyncPostgresSaver(conn=conn)

                try:
                    await checkpointer.setup()
                    self.logger.info("LangGraph checkpointer tables setup completed")
                except psycopg.errors.UniqueViolation as e:
                    # This can happen when multiple agents start simultaneously and race
                    # to create the same database objects (tables, types, indexes, etc.).
                    # This is expected and safe to ignore - it just means another agent
                    # created the object first.
                    self.logger.debug(f"Database objects already exist (parallel setup): {e}")

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

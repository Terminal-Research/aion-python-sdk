from typing import Any, Optional

from aion.shared.agent.adapters import (
    CheckpointerAdapter,
    CheckpointerConfig,
    CheckpointerType,
)
from aion.shared.db import DbManagerProtocol
from aion.shared.logging import get_logger
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

logger = get_logger()


class LangGraphCheckpointerAdapter(CheckpointerAdapter):
    """LangGraph checkpointer adapter with database dependency injection.

    This adapter creates checkpointer instances for different storage backends
    (in-memory, PostgreSQL) based on configuration. For PostgreSQL checkpointers,
    a database manager instance must be provided via dependency injection.
    """

    def __init__(self, db_manager: Optional[DbManagerProtocol] = None):
        """Initialize adapter with optional database manager.

        Args:
            db_manager: Database manager instance for PostgreSQL checkpointer support.
                       If None, PostgreSQL checkpointers will fall back to InMemorySaver.
        """
        self._db_manager = db_manager

    async def create_checkpointer(
            self,
            config: CheckpointerConfig,
    ) -> Any:
        logger.debug(f"Creating checkpointer of type: {config.type}")

        if config.type == CheckpointerType.MEMORY:
            return self._create_memory_checkpointer()

        elif config.type == CheckpointerType.POSTGRES:
            if not self._db_manager:
                logger.warning(
                    "Database manager not provided, falling back to InMemorySaver"
                )
                return self._create_memory_checkpointer()

            postgres_checkpointer = await self._create_postgres_checkpointer()
            if postgres_checkpointer is None:
                logger.warning(
                    "PostgreSQL checkpointer creation failed, falling back to InMemorySaver"
                )
                return self._create_memory_checkpointer()
            return postgres_checkpointer

        else:
            raise ValueError(
                f"Unsupported checkpointer type: {config.type}. "
                f"Supported types: {CheckpointerType.MEMORY}, {CheckpointerType.POSTGRES}"
            )

    async def validate_connection(self, checkpointer: Any) -> bool:
        if isinstance(checkpointer, InMemorySaver):
            return True

        elif isinstance(checkpointer, AsyncPostgresSaver):
            try:
                if not self._db_manager:
                    return False
                return self._db_manager.is_initialized
            except Exception as e:
                logger.error(f"Failed to validate PostgreSQL checkpointer: {e}")
                return False

        return True

    @staticmethod
    def _create_memory_checkpointer() -> InMemorySaver:
        """Create in-memory checkpointer.

        Returns:
            InMemorySaver instance
        """
        return InMemorySaver()

    async def _create_postgres_checkpointer(self) -> Optional[AsyncPostgresSaver]:
        """Create PostgreSQL checkpointer using injected database manager.

        Returns:
            AsyncPostgresSaver instance or None if creation fails
        """
        try:
            if not self._db_manager or not self._db_manager.is_initialized:
                logger.warning("Database manager not initialized")
                return None

            pool = self._db_manager.get_pool()
            if not pool:
                logger.warning("Database pool not available")
                return None

            checkpointer = AsyncPostgresSaver(conn=pool)
            logger.info("Created AsyncPostgresSaver checkpointer")
            return checkpointer

        except Exception as ex:
            logger.error(f"Failed to create PostgreSQL checkpointer: {ex}")
            return None

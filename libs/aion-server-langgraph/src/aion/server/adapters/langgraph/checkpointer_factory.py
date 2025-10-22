from typing import Any, Optional

from aion.shared.logging import get_logger
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from aion.server.adapters.base.checkpointer_adapter import (
    Checkpoint,
    CheckpointerAdapter,
    CheckpointerConfig,
    CheckpointerType,
)
from aion.server.db import db_manager

logger = get_logger()


class LangGraphCheckpointerAdapter(CheckpointerAdapter):
    async def create_checkpointer(
            self,
            config: CheckpointerConfig,
    ) -> Any:
        logger.debug(f"Creating checkpointer of type: {config.type}")

        if config.type == CheckpointerType.MEMORY:
            return self._create_memory_checkpointer()

        elif config.type == CheckpointerType.POSTGRES:
            return await self._create_postgres_checkpointer(config)

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
                return db_manager.is_initialized
            except Exception as e:
                logger.error(f"Failed to validate PostgreSQL checkpointer: {e}")
                return False

        return True

    def _create_memory_checkpointer(self) -> InMemorySaver:
        logger.debug("Creating InMemorySaver checkpointer")
        return InMemorySaver()

    async def _create_postgres_checkpointer(
            self,
            config: CheckpointerConfig,
    ) -> AsyncPostgresSaver:
        try:
            if not db_manager.is_initialized:
                logger.warning(
                    "Database manager not initialized, falling back to InMemorySaver"
                )
                return self._create_memory_checkpointer()
            pool = db_manager.get_pool()
            if not pool:
                logger.warning(
                    "Database pool not available, falling back to InMemorySaver"
                )
                return self._create_memory_checkpointer()
            checkpointer = AsyncPostgresSaver(conn=pool)
            logger.info("Created AsyncPostgresSaver checkpointer")

            return checkpointer

        except Exception as e:
            logger.error(f"Failed to create PostgreSQL checkpointer: {e}")
            logger.warning("Falling back to InMemorySaver")
            return self._create_memory_checkpointer()


from typing import Any, Optional

from aion.shared.agent.adapters.checkpointer_adapter import (
    CheckpointerAdapter,
    CheckpointerConfig,
    CheckpointerType,
)
from aion.shared.logging import get_logger
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

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
                return db_manager.is_initialized
            except Exception as e:
                logger.error(f"Failed to validate PostgreSQL checkpointer: {e}")
                return False

        return True

    @staticmethod
    def _create_memory_checkpointer() -> InMemorySaver:
        return InMemorySaver()

    @staticmethod
    async def _create_postgres_checkpointer() -> Optional[AsyncPostgresSaver]:
        try:
            if not db_manager.is_initialized:
                logger.warning("Database manager not initialized")
                return None

            pool = db_manager.get_pool()
            if not pool:
                logger.warning("Database pool not available")
                return None

            checkpointer = AsyncPostgresSaver(conn=pool)
            logger.info("Created AsyncPostgresSaver checkpointer")
            return checkpointer

        except Exception as ex:
            logger.error(f"Failed to create PostgreSQL checkpointer: {ex}")
            return None

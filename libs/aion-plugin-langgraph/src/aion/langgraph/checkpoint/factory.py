"""LangGraph checkpoint factory."""

from typing import Any, Optional

from aion.shared.db import DbManagerProtocol
from aion.shared.logging import get_logger

from .backends import MemoryBackend, PostgresBackend

logger = get_logger()


class CheckpointerFactory:
    """Factory for creating LangGraph checkpointer instances.

    Selects the appropriate backend based on database availability and returns
    a ready-to-use checkpointer. Falls back to in-memory if PostgreSQL is
    unavailable.
    """

    @classmethod
    async def create(cls, db_manager: Optional[DbManagerProtocol] = None) -> Any:
        """Create a checkpointer using the most appropriate backend.

        Args:
            db_manager: Optional database manager. If provided and initialized,
                        AionAsyncPostgresSaver is used; otherwise falls back to
                        InMemorySaver.

        Returns:
            A checkpointer instance ready for use.
        """
        if db_manager:
            checkpointer = await cls._create_postgres(db_manager)
            if checkpointer is not None:
                return checkpointer

        return await MemoryBackend().create()

    @staticmethod
    async def _create_postgres(db_manager: DbManagerProtocol) -> Any:
        backend = PostgresBackend(db_manager)
        if not backend.is_available():
            logger.warning("PostgreSQL backend unavailable, falling back to memory")
            return None

        checkpointer = await backend.create()
        if checkpointer is None:
            logger.warning("Failed to create PostgreSQL checkpointer, falling back to memory")

        return checkpointer


__all__ = ["CheckpointerFactory"]

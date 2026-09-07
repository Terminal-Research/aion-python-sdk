"""LangGraph checkpoint factory."""

import logging
from typing import Any, Optional

from aion.core.db import DbManagerProtocol

from .backends import MemoryBackend, PostgresBackend

logger = logging.getLogger(__name__)


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
            backend = PostgresBackend(db_manager)
            if backend.is_available():
                checkpointer = await cls._create_postgres(backend)
                if checkpointer is not None:
                    return checkpointer
            else:
                logger.debug("PostgreSQL not configured, using in-memory checkpointer")

        return await MemoryBackend().create()

    @staticmethod
    async def _create_postgres(backend: PostgresBackend) -> Any:
        checkpointer = await backend.create()
        if checkpointer is None:
            logger.warning("Failed to create PostgreSQL checkpointer, falling back to memory")

        return checkpointer


__all__ = ["CheckpointerFactory"]

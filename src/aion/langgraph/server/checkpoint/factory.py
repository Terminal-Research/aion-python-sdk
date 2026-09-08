"""LangGraph checkpoint factory."""

import logging
from typing import Any, Optional

from aion.core.db import DbManagerProtocol

from .backends import MemoryBackend, PostgresBackend

logger = logging.getLogger(__name__)

POSTGRES_INIT_FAILED = (
    "Configured PostgreSQL checkpointer failed to initialize; refusing to "
    "fall back to an in-memory store. Unset POSTGRES_URL to run in-memory."
)


class CheckpointerFactory:
    """Factory for creating LangGraph checkpointer instances.

    Uses PostgreSQL when an initialized database manager is supplied and
    in-memory otherwise. A configured PostgreSQL that fails to initialize
    stops startup instead of degrading to an in-memory checkpointer.
    """

    @classmethod
    async def create(cls, db_manager: Optional[DbManagerProtocol] = None) -> Any:
        """Create a checkpointer using the configured backend.

        Args:
            db_manager: Optional database manager. If provided and initialized,
                        AionAsyncPostgresSaver is used; otherwise InMemorySaver.

        Returns:
            A checkpointer instance ready for use.

        Raises:
            RuntimeError: The database manager is initialized but the
                PostgreSQL checkpointer could not be created.
        """
        backend = PostgresBackend(db_manager) if db_manager else None

        if backend is not None and backend.is_available():
            try:
                checkpointer = await backend.create()
            except Exception as exc:
                logger.error("Failed to create PostgreSQL checkpointer", exc_info=exc)
                raise RuntimeError(POSTGRES_INIT_FAILED) from exc
        else:
            logger.debug("PostgreSQL not configured, using in-memory checkpointer")
            checkpointer = await MemoryBackend().create()

        logger.info(f"Initialized {type(checkpointer).__name__}")
        return checkpointer


__all__ = ["CheckpointerFactory"]

"""Session service factory for ADK server package.

This module provides the SessionServiceFactory for creating session service
instances by selecting the appropriate storage backend.
"""

import logging
from typing import Optional

from aion.core.db import DbManagerProtocol
from google.adk.sessions import BaseSessionService

from .backends import PostgresBackend, MemoryBackend

logger = logging.getLogger(__name__)

POSTGRES_INIT_FAILED = (
    "Configured PostgreSQL session service failed to initialize; refusing to "
    "fall back to an in-memory store. Unset POSTGRES_URL to run in-memory."
)


class SessionServiceFactory:
    """Factory for creating ADK session service instances.

    Uses PostgreSQL when an initialized database manager is supplied and
    in-memory otherwise. A configured PostgreSQL that fails to initialize
    stops startup instead of degrading to an in-memory session service.
    """

    @classmethod
    async def create(cls, db_manager: Optional[DbManagerProtocol] = None) -> BaseSessionService:
        """Create session service using the configured backend.

        Args:
            db_manager: Optional database manager. If provided and initialized,
                        AionADKSessionService is used; otherwise
                        InMemorySessionService.

        Returns:
            BaseSessionService: A session service instance

        Raises:
            RuntimeError: The database manager is initialized but the
                PostgreSQL session service could not be created.
        """
        backend = PostgresBackend(db_manager) if db_manager else None

        if backend is not None and backend.is_available():
            try:
                service = await backend.create()
            except Exception as exc:
                logger.error("Failed to create PostgreSQL session service", exc_info=exc)
                raise RuntimeError(POSTGRES_INIT_FAILED) from exc
        else:
            logger.debug("PostgreSQL not configured, using in-memory session service")
            service = await MemoryBackend().create()

        logger.info(f"Initialized {type(service).__name__}")
        return service


__all__ = ["SessionServiceFactory"]

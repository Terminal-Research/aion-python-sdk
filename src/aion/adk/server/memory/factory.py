"""The memory service the server gives an ADK agent."""

from __future__ import annotations

import logging
from typing import Optional

from google.adk.memory import BaseMemoryService, InMemoryMemoryService

from aion.core.db import DbManagerProtocol

__all__ = ["MemoryServiceFactory"]

logger = logging.getLogger(__name__)


class MemoryServiceFactory:
    """Creates the server's memory service.

    PostgreSQL when an initialized database manager is supplied - the
    sessions' database and schema - and ADK's in-memory service otherwise,
    whose memory is gone when the server stops, as the in-memory sessions are.
    """

    @classmethod
    def create(cls, db_manager: Optional[DbManagerProtocol] = None) -> BaseMemoryService:
        """Create the memory service the agent's Runner is given."""
        if db_manager is not None and db_manager.is_initialized:
            from .postgres import AionADKMemoryService

            service: BaseMemoryService = AionADKMemoryService(engine=db_manager.get_engine())
        else:
            logger.debug("PostgreSQL not configured, using an in-memory memory service")
            service = InMemoryMemoryService()
        logger.info("Initialized the memory service %s", type(service).__name__)
        return service

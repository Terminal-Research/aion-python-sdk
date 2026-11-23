"""The store the server gives a graph that has none of its own."""

from __future__ import annotations

import logging
from typing import Optional

from langgraph.store.memory import InMemoryStore

from aion.core.db import DbManagerProtocol
from aion.db.postgres.constants import AION_SCHEMA
from aion.langgraph.server.constants import AION_LANGGRAPH_SCHEMA

from .owner import OwnerScopedStore

__all__ = ["StoreFactory"]

logger = logging.getLogger(__name__)


class StoreFactory:
    """Creates the server's store, wrapped so each agent's and owner's data stays apart.

    PostgreSQL when an initialized database manager is supplied - the
    checkpoints' database and schema - and memory otherwise, whose data is
    gone when the server stops, as the in-memory checkpoints are.
    """

    @classmethod
    async def create(cls, db_manager: Optional[DbManagerProtocol] = None) -> Optional[OwnerScopedStore]:
        """Create the store for a graph the server compiles or attaches it to.

        None on PostgreSQL with a langgraph-checkpoint-postgres older than
        ``POSTGRES_STORE_SINCE``, whose store cannot run on the server's pool:
        the graph gets no store, and the log says so once per graph.
        """
        if db_manager is not None and db_manager.is_initialized:
            from .postgres import POSTGRES_STORE_SINCE, AionAsyncPostgresStore, postgres_store_supported

            if not postgres_store_supported():
                logger.warning(
                    "The installed langgraph-checkpoint-postgres keeps its store on a single connection, "
                    "not the server's pool, so the graph gets no store from the server; version %s or "
                    "later gives it one",
                    POSTGRES_STORE_SINCE,
                )
                return None
            store = AionAsyncPostgresStore(
                conn=db_manager.get_pool(), schema=AION_LANGGRAPH_SCHEMA, restore_schema=AION_SCHEMA
            )
        else:
            logger.debug("PostgreSQL not configured, using an in-memory store")
            store = InMemoryStore()
        logger.info("Initialized the store %s", type(store).__name__)
        return OwnerScopedStore(store)

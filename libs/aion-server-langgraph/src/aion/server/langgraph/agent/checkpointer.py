from typing import Literal

from aion.shared.logging import get_logger
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import Graph

from aion.server.db import db_manager

logger = get_logger()

CHECKPOINTER_TYPE = Literal["memory", "postgres"]


class GraphCheckpointerManager:
    """Manager for creating and configuring graph checkpointers.

    This class handles the creation of appropriate checkpointer instances
    based on the available database configuration. It automatically selects
    between PostgreSQL and in-memory checkpointers depending on whether
    a database connection is available.

    The checkpointer is used by LangGraph to persist conversation state
    and enable features like conversation resumption and state recovery.
    """

    _checkpointer_type: CHECKPOINTER_TYPE

    def __init__(self, graph: Graph):
        if not isinstance(graph, Graph):
            raise ValueError(f"Graph value is not instance of Graph, got {type(graph)}")

        self.graph = graph

    @property
    def checkpointer_type(self) -> CHECKPOINTER_TYPE:
        """Determine and return the appropriate checkpointer type.

        Automatically selects the checkpointer type based on database availability:
        - "postgres": If database manager is initialized and connected
        - "memory": If no database connection is available (fallback)
        """
        if hasattr(self, "_checkpointer_type"):
            return self._checkpointer_type

        if db_manager.is_initialized:
            self._checkpointer_type = "postgres"
        else:
            self._checkpointer_type = "memory"

        return self._checkpointer_type

    def get_checkpointer(self):
        """Create and return an appropriate checkpointer instance.

        Creates either a PostgreSQL-based or in-memory checkpointer based on
        the determined checkpointer type.
        """
        if self.checkpointer_type == "postgres":
            return self._get_postgres_checkpointer()
        else:
            return self._get_memory_checkpointer()

    def _get_memory_checkpointer(self):
        """Create an in-memory checkpointer instance.

        Creates a basic in-memory checkpointer that stores conversation state
        in RAM. This checkpointer is ephemeral and will lose state when the
        application restarts, but requires no external dependencies.
        """
        logger.debug(f"Created InMemorySaver checkpointer")
        return InMemorySaver()

    def _get_postgres_checkpointer(self):
        """Create a PostgreSQL-based checkpointer instance with fallback.

        Attempts to create a PostgreSQL checkpointer using the database manager's
        connection pool. If creation fails (due to connection issues, missing
        tables, etc.), automatically falls back to an in-memory checkpointer
        to ensure the application continues to function.
        """
        try:
            checkpointer = AsyncPostgresSaver(conn=db_manager.get_pool())
            logger.info(f"Created PostgresSaver checkpointer")
            return checkpointer

        except Exception as e:
            logger.error(f"Failed to create PostgresSaver: {e}")
            logger.warning("Falling back to InMemorySaver")
            return InMemorySaver()

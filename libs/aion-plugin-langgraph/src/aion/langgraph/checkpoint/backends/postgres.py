"""PostgreSQL checkpointer backend."""

from contextlib import asynccontextmanager
from typing import Any, Optional

from psycopg import AsyncConnection, AsyncCursor, sql
from psycopg_pool import AsyncConnectionPool

from aion.db.postgres.constants import AION_SCHEMA
from aion.shared.db import DbManagerProtocol
from aion.shared.logging import get_logger
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from aion.langgraph.constants import AION_LANGGRAPH_SCHEMA
from .base import CheckpointerBackend

logger = get_logger()


class AionAsyncPostgresSaver(AsyncPostgresSaver):
    """AsyncPostgresSaver that routes all queries to a dedicated PostgreSQL schema.

    Extends AsyncPostgresSaver to support schema isolation by setting search_path
    on each connection before executing queries and restoring it afterwards.
    This allows sharing a single connection pool across multiple schemas without
    creating additional connections.

    Attributes:
        _schema: Target schema for LangGraph checkpoint tables.
        _restore_schema: Schema to restore after each operation (typically the default app schema).
    """

    def __init__(self, conn, schema: str, restore_schema: str, **kwargs):
        super().__init__(conn, **kwargs)
        self._schema = schema
        self._restore_schema = restore_schema

    @staticmethod
    async def _ensure_schema_exists(conn: AsyncConnection[Any], schema: str) -> None:
        """Create the given schema if it does not exist."""
        await conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))

    @staticmethod
    async def _set_search_path(conn: AsyncConnection[Any] | AsyncCursor[Any], schema: str) -> None:
        """Set PostgreSQL search_path to the given schema on the connection or cursor."""
        await conn.execute(sql.SQL("SET search_path = {}").format(sql.Identifier(schema)))

    async def setup(self) -> None:
        """Create the target schema if not exists and run LangGraph migrations.

        Checks out a dedicated connection from the pool with autocommit enabled,
        required by CREATE INDEX CONCURRENTLY used in LangGraph migrations.
        """
        if isinstance(self.conn, AsyncConnectionPool):
            async with self.conn.connection() as conn:
                await conn.set_autocommit(True)
                await self._ensure_schema_exists(conn, self._schema)
                await self._set_search_path(conn, self._schema)
                await AsyncPostgresSaver(conn=conn).setup()
        else:
            await self._ensure_schema_exists(self.conn, self._schema)
            await self._set_search_path(self.conn, self._schema)
            await AsyncPostgresSaver(conn=self.conn).setup()

    @asynccontextmanager
    async def _cursor(self, *, pipeline: bool = False):
        """Yield a cursor scoped to the target schema.

        Sets search_path to the target schema before yielding and restores
        the original schema in a finally block to ensure the connection
        is returned to the pool in a clean state.
        """
        async with super()._cursor(pipeline=pipeline) as cur:
            await self._set_search_path(cur, self._schema)
            try:
                yield cur
            finally:
                await self._set_search_path(cur, self._restore_schema)


class PostgresBackend(CheckpointerBackend):
    """PostgreSQL checkpointer backend using a shared connection pool.

    Creates an AionAsyncPostgresSaver scoped to the dedicated LangGraph schema
    (AION_LANGGRAPH_SCHEMA), reusing the application's existing connection pool
    without allocating additional connections.

    Attributes:
        _db_manager: Database manager providing the shared connection pool.
    """

    def __init__(self, db_manager: DbManagerProtocol):
        self._db_manager = db_manager

    def is_available(self) -> bool:
        """Return True if the database manager is initialized and the pool is ready."""
        return self._db_manager is not None and self._db_manager.is_initialized

    async def create(self) -> Optional[AionAsyncPostgresSaver]:
        """Initialize and return a configured AionAsyncPostgresSaver.

        Runs LangGraph migrations in the target schema on first call,
        then returns a checkpointer bound to the shared pool.

        Returns:
            AionAsyncPostgresSaver instance, or None if the pool is unavailable.
        """
        pool = self._db_manager.get_pool()
        if not pool:
            logger.warning("Database pool not available")
            return None

        checkpointer = AionAsyncPostgresSaver(
            conn=pool,
            schema=AION_LANGGRAPH_SCHEMA,
            restore_schema=AION_SCHEMA,
        )
        await checkpointer.setup()
        logger.info("LangGraph checkpointer tables setup completed")
        return checkpointer


__all__ = ["PostgresBackend"]

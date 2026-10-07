"""PostgreSQL checkpointer backend."""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from psycopg import AsyncConnection, AsyncCursor, sql
from psycopg.pq import TransactionStatus
from psycopg_pool import AsyncConnectionPool

from aion.db.postgres.constants import AION_SCHEMA
from aion.core.db import DbManagerProtocol
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from aion.langgraph.server.constants import AION_LANGGRAPH_SCHEMA
from .base import CheckpointerBackend

logger = logging.getLogger(__name__)

# Every server of an agent runs the checkpoint setup on start, and in cluster
# mode they share one database. The setup checks for its schema and tables and
# then creates them, so servers starting together against a fresh database
# would all create, and all but one would fail on PostgreSQL's catalog
# constraints. They take turns under this session-level advisory lock, which
# is distinct from the SDK migration lock so neither setup waits on the other.
CHECKPOINT_SETUP_LOCK_KEY = 7_382_194_612

# How often a server waiting for that lock asks for it again. The lock is
# polled rather than waited for: LangGraph's migrations build indexes
# CONCURRENTLY, which waits for every open transaction, and a statement blocked
# in pg_advisory_lock is one - the holder and the waiter would deadlock.
CHECKPOINT_SETUP_LOCK_POLL_SECONDS = 0.1


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
        The whole setup runs under ``CHECKPOINT_SETUP_LOCK_KEY``, so servers
        that start together against one database run it one after another.
        """
        if isinstance(self.conn, AsyncConnectionPool):
            async with self.conn.connection() as conn:
                await conn.set_autocommit(True)
                await self._locked_setup(conn)
        else:
            await self._locked_setup(self.conn)

    async def _locked_setup(self, conn: AsyncConnection[Any]) -> None:
        """Run the schema and table setup on ``conn`` while holding the setup lock."""
        while not await self._try_setup_lock(conn):
            await asyncio.sleep(CHECKPOINT_SETUP_LOCK_POLL_SECONDS)
        try:
            await self._ensure_schema_exists(conn, self._schema)
            await self._set_search_path(conn, self._schema)
            await AsyncPostgresSaver(conn=conn).setup()
        finally:
            # A session-level lock survives a rolled-back transaction, and an
            # aborted one would refuse the unlock, so roll back first.
            if conn.info.transaction_status == TransactionStatus.INERROR:
                await conn.rollback()
            await conn.execute(
                "SELECT pg_advisory_unlock(%s::bigint)", (CHECKPOINT_SETUP_LOCK_KEY,)
            )

    @staticmethod
    async def _try_setup_lock(conn: AsyncConnection[Any]) -> bool:
        """Take the setup lock if no other session holds it, without waiting."""
        cursor = await conn.execute(
            "SELECT pg_try_advisory_lock(%s::bigint)", (CHECKPOINT_SETUP_LOCK_KEY,)
        )
        row = await cursor.fetchone()
        return bool(row and row[0])

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

    async def create(self) -> AionAsyncPostgresSaver:
        """Initialize and return a configured AionAsyncPostgresSaver.

        Runs LangGraph migrations in the target schema on first call,
        then returns a checkpointer bound to the shared pool.

        Returns:
            AionAsyncPostgresSaver instance bound to the shared pool.

        Raises:
            Exception: Propagated from pool acquisition or the migration run.
                A configured PostgreSQL that fails here must stop startup
                rather than silently degrade to an in-memory store.
        """
        pool = self._db_manager.get_pool()

        checkpointer = AionAsyncPostgresSaver(
            conn=pool,
            schema=AION_LANGGRAPH_SCHEMA,
            restore_schema=AION_SCHEMA,
        )
        await checkpointer.setup()
        logger.info("LangGraph checkpointer tables setup completed")
        return checkpointer


__all__ = ["PostgresBackend"]

"""The server's store on PostgreSQL: LangGraph's own store tables, in the checkpoint schema."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from langgraph.store.postgres.aio import AsyncPostgresStore
from psycopg import AsyncConnection, AsyncCursor, sql
from psycopg_pool import AsyncConnectionPool

from aion.core.db import DbManagerProtocol
from aion.db.postgres.constants import AION_SCHEMA
from aion.db.postgres.migrations import SchemaCheck, SchemaState
from aion.langgraph.server.checkpoint.backends.postgres import run_under_setup_lock
from aion.langgraph.server.constants import AION_LANGGRAPH_SCHEMA

__all__ = [
    "AionAsyncPostgresStore",
    "POSTGRES_STORE_SINCE",
    "check_store_tables",
    "migrate_store_tables",
    "postgres_store_supported",
]

POSTGRES_STORE_SINCE = "2.0.4"
"""The first langgraph-checkpoint-postgres whose store runs on a connection pool, as the server's must."""


def postgres_store_supported() -> bool:
    """Whether the installed langgraph-checkpoint-postgres can keep the store on the server's pool.

    Its store takes a pool, and opens every cursor through ``_cursor``, from
    ``POSTGRES_STORE_SINCE``; an older one works on a single connection only.
    """
    return hasattr(AsyncPostgresStore, "_cursor")

STORE_SCHEMA_NAME = "LangGraph store tables"


class AionAsyncPostgresStore(AsyncPostgresStore):
    """AsyncPostgresStore whose queries run in the LangGraph schema, on the application's pool.

    LangGraph's store names its tables without a schema. Each cursor sets
    ``search_path`` to ``schema`` before it is used and restores
    ``restore_schema`` after, so the pool's connections go back as they came.
    """

    __slots__ = ("_schema", "_restore_schema")

    def __init__(self, conn: Any, schema: str, restore_schema: str, **kwargs: Any) -> None:
        super().__init__(conn, **kwargs)
        self._schema = schema
        self._restore_schema = restore_schema

    @asynccontextmanager
    async def _cursor(self, *, pipeline: bool = False):
        """Yield a cursor scoped to the LangGraph schema, restoring the pool's own afterwards."""
        async with super()._cursor(pipeline=pipeline) as cur:
            await _set_search_path(cur, self._schema)
            try:
                yield cur
            finally:
                await _set_search_path(cur, self._restore_schema)


async def _set_search_path(conn: AsyncConnection[Any] | AsyncCursor[Any], schema: str) -> None:
    await conn.execute(sql.SQL("SET search_path = {}").format(sql.Identifier(schema)))


async def migrate_store_tables(db_manager: DbManagerProtocol) -> None:
    """Run LangGraph's store migrations in the LangGraph schema.

    On a connection of its own with autocommit, which the migrations need -
    they build an index CONCURRENTLY - and under the lock the checkpoint
    setup takes, so servers that start together run them one after another.
    """
    pool: AsyncConnectionPool = db_manager.get_pool()
    async with pool.connection() as conn:
        await conn.set_autocommit(True)

        async def setup() -> None:
            await conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(AION_LANGGRAPH_SCHEMA)))
            await _set_search_path(conn, AION_LANGGRAPH_SCHEMA)
            try:
                await AsyncPostgresStore(conn=conn).setup()
            finally:
                await _set_search_path(conn, AION_SCHEMA)

        await run_under_setup_lock(conn, setup)


async def check_store_tables(db_manager: DbManagerProtocol) -> SchemaCheck:
    """Compare the applied store migrations with the installed LangGraph's. Read-only.

    LangGraph records each store migration it applies as a row of
    ``store_migrations``; ``AsyncPostgresStore.MIGRATIONS`` is the list the
    installed version knows.
    """
    latest = len(AsyncPostgresStore.MIGRATIONS) - 1
    async with db_manager.get_pool().connection() as conn:
        cursor = await conn.execute("SELECT to_regclass(%s)", (f"{AION_LANGGRAPH_SCHEMA}.store_migrations",))
        row = await cursor.fetchone()
        if row is None or row[0] is None:
            return SchemaCheck(STORE_SCHEMA_NAME, SchemaState.BEHIND, "not migrated")
        cursor = await conn.execute(
            sql.SQL("SELECT max(v) FROM {}.store_migrations").format(sql.Identifier(AION_LANGGRAPH_SCHEMA))
        )
        row = await cursor.fetchone()
    applied = -1 if row is None or row[0] is None else row[0]
    detail = f"migration {applied} applied; the installed LangGraph's newest is {latest}"
    if applied > latest:
        return SchemaCheck(STORE_SCHEMA_NAME, SchemaState.AHEAD, detail)
    if applied < latest:
        return SchemaCheck(STORE_SCHEMA_NAME, SchemaState.BEHIND, detail)
    return SchemaCheck(STORE_SCHEMA_NAME, SchemaState.CURRENT, detail)

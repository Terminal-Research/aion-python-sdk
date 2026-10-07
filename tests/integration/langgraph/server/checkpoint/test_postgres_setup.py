"""LangGraph checkpoint tables on a PostgreSQL that several servers share.

In cluster mode every server of an agent starts against the same database,
and on a fresh one they all find the checkpoint tables missing at once. Each
start runs the checkpointer setup on its own connection here, which is what
separate server processes do.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from psycopg import sql
from psycopg_pool import AsyncConnectionPool

from aion.db.postgres.constants import AION_SCHEMA
from aion.langgraph.server.checkpoint.backends.postgres import AionAsyncPostgresSaver

POSTGRES_TEST_URL = os.getenv("POSTGRES_TEST_URL")

pytestmark = pytest.mark.skipif(not POSTGRES_TEST_URL, reason="POSTGRES_TEST_URL is not set")

STARTS = 8
"""Concurrent first starts; enough to collide on the catalog every run."""


async def test_concurrent_first_starts_on_a_fresh_database_all_succeed():
    schema = f"langgraph_setup_{uuid.uuid4().hex[:12]}"
    async with AsyncConnectionPool(
        POSTGRES_TEST_URL, min_size=STARTS, max_size=STARTS, open=False
    ) as pool:
        try:
            await asyncio.gather(*(
                AionAsyncPostgresSaver(
                    conn=pool, schema=schema, restore_schema=AION_SCHEMA
                ).setup()
                for _ in range(STARTS)
            ))
        finally:
            async with pool.connection() as conn:
                await conn.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
                )

"""The push notification config table on a PostgreSQL that several servers share.

In cluster mode every server of an agent starts against the same database,
and on a fresh one they all find the config table missing at once. Each start
here is a store of its own over its own engine, which is what separate server
processes are.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from aion.db.postgres.utils import convert_pg_url
from aion.server.tasks.push_config_store import AionDatabasePushNotificationConfigStore

POSTGRES_TEST_URL = os.getenv("POSTGRES_TEST_URL")

pytestmark = pytest.mark.skipif(not POSTGRES_TEST_URL, reason="POSTGRES_TEST_URL is not set")

STARTS = 8
"""Concurrent first starts; enough to collide on the catalog every run."""


async def test_concurrent_first_starts_on_a_fresh_database_all_succeed():
    schema = f"push_setup_{uuid.uuid4().hex[:12]}"
    url = convert_pg_url(POSTGRES_TEST_URL, driver="psycopg")
    engines = [create_async_engine(url) for _ in range(STARTS)]
    async with engines[0].begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    # Each engine connects before the starts begin, so they reach the table
    # check together rather than spread out over their connection setup.
    for engine in engines:
        async with engine.connect():
            pass
    try:
        await asyncio.gather(*(
            AionDatabasePushNotificationConfigStore(
                engine=engine.execution_options(schema_translate_map={None: schema}),
            ).initialize()
            for engine in engines
        ))
    finally:
        async with engines[0].begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        for engine in engines:
            await engine.dispose()

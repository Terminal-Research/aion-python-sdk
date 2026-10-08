"""The pre-migration permission check on a database another server is migrating.

In cluster mode every server of an agent starts against the same database and
checks its permissions before it migrates. On a fresh database one of them is
already creating the schema under the migration lock while the others check,
and the check creates that schema too.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid

import psycopg
import pytest

from aion.db.postgres.constants import MIGRATION_ADVISORY_LOCK_KEY
from aion.db.postgres.utils import convert_pg_url, validate_permissions

POSTGRES_TEST_URL = os.getenv("POSTGRES_TEST_URL")

pytestmark = pytest.mark.skipif(not POSTGRES_TEST_URL, reason="POSTGRES_TEST_URL is not set")

CHECKS = 8
"""Concurrent permission checks; each one would collide with the migration."""


async def _wait_until_blocked(url: str, count: int) -> None:
    """Return once ``count`` sessions of this database wait on a lock.

    Polled on a connection of its own, in autocommit: within one transaction
    ``pg_stat_activity`` keeps answering from the snapshot its first read took.
    """
    deadline = time.monotonic() + 10
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as monitor:
        while True:
            cursor = await monitor.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND cardinality(pg_blocking_pids(pid)) > 0"
            )
            (blocked,) = await cursor.fetchone()
            if blocked >= count:
                return
            if time.monotonic() > deadline:
                raise AssertionError(f"{blocked} of {count} permission checks reached the database")
            await asyncio.sleep(0.05)


async def test_permission_checks_wait_for_a_migration_creating_the_schema():
    schema = f"permission_check_{uuid.uuid4().hex[:12]}"
    url = convert_pg_url(POSTGRES_TEST_URL, driver=None)
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as runner:
        # What a migration runner does first: take the migration lock for its
        # session, then create the schema and commit. The checks start while
        # the schema is created but not yet committed.
        await runner.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_ADVISORY_LOCK_KEY,))
        try:
            async with runner.transaction():
                await runner.execute(f'CREATE SCHEMA "{schema}"')
                checks = asyncio.gather(
                    *(validate_permissions(url, schema=schema) for _ in range(CHECKS))
                )
                await _wait_until_blocked(url, CHECKS)
        finally:
            await runner.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_ADVISORY_LOCK_KEY,))
        try:
            results = await checks
        finally:
            await runner.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')

    assert [result.get("table_error") for result in results] == [None] * CHECKS
    assert all(result["can_create_table"] for result in results)

"""Migrating and checking the whole database: the SDK's tables and each framework's.

The tests drop the SDK's schemas to start from an empty database, and migrate
everything again when they finish, so the tests after them find the tables.
"""

from __future__ import annotations

import logging

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import text

from tests.integration.server.tasks.postgres_support import POSTGRES_TEST_URL, prepared_database

from aion.db.postgres.constants import ADK_SCHEMA, AION_SCHEMA, LANGGRAPH_SCHEMA  # noqa: E402
from aion.db.postgres.migrations import SchemaState, upgrade_to_head  # noqa: E402
from aion.db.postgres.migrations.env import config as alembic_config  # noqa: E402
from aion.db.settings import db_settings  # noqa: E402
from aion.server.database import (  # noqa: E402
    DatabaseBehindError,
    check_database,
    migrate_database,
    prepare_database,
)

pytestmark = pytest.mark.skipif(not POSTGRES_TEST_URL, reason="POSTGRES_TEST_URL is not set")


@pytest.fixture
async def db():
    async with prepared_database() as manager:
        try:
            yield manager
        finally:
            await migrate_database(manager)


async def _execute(db, statement: str) -> None:
    async with db.get_session() as session:
        await session.execute(text(statement))
        await session.commit()


async def _empty(db) -> None:
    for schema in (AION_SCHEMA, LANGGRAPH_SCHEMA, ADK_SCHEMA):
        await _execute(db, f"DROP SCHEMA IF EXISTS {schema} CASCADE")


def _states(checks) -> dict[str, SchemaState]:
    return {check.name: check.state for check in checks}


async def test_an_empty_database_is_migrated_in_full(db) -> None:
    await _empty(db)
    before = await check_database(db)

    await migrate_database(db)

    after = await check_database(db)
    assert set(_states(before).values()) == {SchemaState.BEHIND}
    assert len(after) == 3, "the SDK's tables, LangGraph's and ADK's"
    assert set(_states(after).values()) == {SchemaState.CURRENT}


async def test_a_server_that_may_not_migrate_refuses_a_database_behind(db, monkeypatch) -> None:
    monkeypatch.setattr(db_settings, "migrate_on_start", False)
    await _empty(db)

    with pytest.raises(DatabaseBehindError, match="aion db migrate"):
        await prepare_database(db)

    await migrate_database(db)
    await prepare_database(db)


async def test_a_database_newer_than_the_code_is_left_as_it_is_and_served(db, monkeypatch, caplog) -> None:
    """Migrations only add, so older code runs on a newer schema after a rollback."""
    head = ScriptDirectory.from_config(alembic_config).get_current_head()
    await _execute(db, f"UPDATE {AION_SCHEMA}.alembic_version SET version_num = '999'")
    await _execute(db, f"INSERT INTO {LANGGRAPH_SCHEMA}.checkpoint_migrations (v) VALUES (999)")
    try:
        states = _states(await check_database(db))
        with caplog.at_level(logging.WARNING):
            await upgrade_to_head()
            await migrate_database(db)
        monkeypatch.setattr(db_settings, "migrate_on_start", False)
        await prepare_database(db)
    finally:
        await _execute(db, f"UPDATE {AION_SCHEMA}.alembic_version SET version_num = '{head}'")
        await _execute(db, f"DELETE FROM {LANGGRAPH_SCHEMA}.checkpoint_migrations WHERE v = 999")

    assert states["SDK tables"] is SchemaState.AHEAD
    assert states["LangGraph checkpoint tables"] is SchemaState.AHEAD
    assert "newer than this SDK version" in caplog.text

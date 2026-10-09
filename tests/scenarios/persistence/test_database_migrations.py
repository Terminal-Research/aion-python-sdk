"""Migrating the database: `aion db migrate`, `aion db check` and DB_MIGRATE_ON_START.

An operator either lets every server migrate the database when it starts, or
migrates it once with `aion db migrate` and starts the servers with
``DB_MIGRATE_ON_START=false`` and a database user that cannot change the
schema. `aion db check` says which state a database is in. A rollback leaves a
database newer than the code that serves it, and that code still runs.

Every scenario here starts from an empty database of its own, created for it
and dropped afterwards: the rest of the group shares one database, which is
migrated long before these scenarios run.
"""

from __future__ import annotations

from typing import Iterator

import psycopg
import pytest

from tests.scenarios.commands import echo_text
from tests.scenarios.harness import ScenarioClient, ServeProcess, ServeVariant, final_task, reply_texts, run_aion
from tests.scenarios.harness.pg import empty_database, user_without_schema_rights

pytestmark = [pytest.mark.persistence]


@pytest.fixture
def database() -> Iterator[str]:
    """The URL of an empty database for this scenario alone."""
    with empty_database() as url:
        yield url


def _variant(url: str, **env: str) -> ServeVariant:
    return ServeVariant(name="default", env={"POSTGRES_URL": url, **env})


def _aion_db(command: str, url: str):
    return run_aion("db", command, env={"POSTGRES_URL": url})


async def _echo(server: ServeProcess, text: str) -> list:
    client = await ScenarioClient.connect(server.base_url, server.variant.agent_id)
    try:
        return await client.send(f"echo {text}")
    finally:
        await client.close()


async def test_aion_db_check_finds_an_empty_database_behind_until_aion_db_migrate(database: str) -> None:
    before = _aion_db("check", database)
    migrate = _aion_db("migrate", database)
    after = _aion_db("check", database)

    assert before.returncode != 0, before.stdout
    assert "aion db migrate" in before.stdout
    assert migrate.returncode == 0, migrate.stdout
    assert after.returncode == 0, after.stdout
    assert "behind" not in after.stdout


@pytest.mark.command("echo")
async def test_a_server_migrates_an_empty_database_when_it_starts(framework, database: str) -> None:
    server = ServeProcess(framework, _variant(database)).start()
    try:
        events = await _echo(server, "migrated-on-start")
    finally:
        server.stop()

    assert final_task(events).state == "COMPLETED"
    assert echo_text("migrated-on-start") in reply_texts(events)
    check = _aion_db("check", database)
    assert check.returncode == 0, check.stdout


@pytest.mark.command("echo")
async def test_a_server_that_may_not_migrate_refuses_an_empty_database(framework, database: str) -> None:
    server = ServeProcess(framework, _variant(database, DB_MIGRATE_ON_START="false"))

    with pytest.raises(RuntimeError, match="aion db migrate"):
        server.start()


@pytest.mark.command("echo")
async def test_a_server_without_schema_rights_serves_a_database_migrated_beforehand(
    framework, database: str
) -> None:
    """Every turn writes the task and the framework's conversation state."""
    migrate = _aion_db("migrate", database)
    assert migrate.returncode == 0, migrate.stdout

    with user_without_schema_rights(database) as restricted:
        server = ServeProcess(framework, _variant(restricted, DB_MIGRATE_ON_START="false")).start()
        try:
            first = await _echo(server, "first")
            second = await _echo(server, "second")
        finally:
            server.stop()

    assert final_task(first).state == "COMPLETED"
    assert final_task(second).state == "COMPLETED"
    assert echo_text("second") in reply_texts(second)


@pytest.mark.command("echo")
async def test_a_database_newer_than_the_code_is_reported_and_served(framework, database: str) -> None:
    """What a rollback leaves behind: migrations only add, so the older code runs on it."""
    assert _aion_db("migrate", database).returncode == 0
    with psycopg.connect(database, autocommit=True) as connection:
        connection.execute("UPDATE aion.alembic_version SET version_num = '999'")

    check = _aion_db("check", database)
    server = ServeProcess(framework, _variant(database)).start()
    try:
        events = await _echo(server, "after-rollback")
        log = server.log_path.read_text(encoding="utf-8", errors="replace")
    finally:
        server.stop()

    assert check.returncode == 0, check.stdout
    assert "ahead" in check.stdout
    assert final_task(events).state == "COMPLETED"
    assert "newer than this SDK version" in log

"""ADK artifact versions numbered by several servers of one agent over one PostgreSQL.

Each ``A2AArtifactService`` here is a server of its own: its memory is its
own, and only the database is shared. A version's artifact reaches the tasks
table later, with the task that carries it, as the event consumer writes it;
the tests write it the same way once the saves are done, and read it back
from inside a turn of the context's holder, as ADK reads it.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import pytest_asyncio
from google.genai import types
from sqlalchemy import text

from tests.integration.adk.server.postgres_turns import PostgresAgent, private, runtime_provider
from tests.integration.server.tasks.postgres_support import POSTGRES_TEST_URL, prepared_database

from unittest.mock import Mock

from google.adk.agents import BaseAgent
from google.adk.memory import InMemoryMemoryService
from google.adk.sessions import InMemorySessionService

from aion.adk.server.artifacts.backends.a2a import A2AArtifactService
from aion.adk.server.execution.adk_executor import ADKExecutor
from aion.adk.server.tables import migrate_aion_tables
from aion.db.postgres.constants import ADK_SCHEMA, AION_SCHEMA
from aion.server.agent.adapters import ExecutionConfig, StateScope

pytestmark = [
    pytest.mark.skipif(not POSTGRES_TEST_URL, reason="POSTGRES_TEST_URL is not set"),
    pytest.mark.asyncio(loop_scope="module"),
]

USER = "alice"
"""The owner the tasks are written as, and the state owner of the private context she holds."""

FILENAME = "report.txt"
SAVES = 6
"""Saves per server: enough for the two to meet on the same numbers every run."""


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def db():
    async with prepared_database() as manager:
        yield manager


@pytest.fixture(autouse=True)
def _runtime_provider():
    with runtime_provider():
        yield


@pytest_asyncio.fixture(loop_scope="module")
async def services(db):
    started: list[A2AArtifactService] = []

    def start() -> A2AArtifactService:
        service = A2AArtifactService(db_manager=db)
        started.append(service)
        return service

    yield start
    for service in started:
        await service.close()


async def _save(service: A2AArtifactService, agent_id: str, context_id: str, text_: str) -> int:
    return await service.save_artifact(
        app_name=agent_id, user_id=USER, session_id=context_id, filename=FILENAME, artifact=types.Part(text=text_)
    )


async def _store_version(agent_id: str, context_id: str, version: int, text_: str) -> None:
    """Write the version's artifact into a task, as the event consumer of the server that saved it does."""
    await PostgresAgent(agent_id).store_artifact(context_id, private(USER), FILENAME, version, text_)


async def _turn(agent_id: str, context_id: str) -> None:
    """Install the history of a turn of ``USER``, who holds the context."""
    agent = PostgresAgent(agent_id)
    assert await agent.admit(context_id, private(USER))
    agent.turn(context_id, private(USER))


async def _recorded(db, agent_id: str, context_id: str) -> list[int]:
    async with db.get_session() as session:
        rows = await session.execute(
            text(
                f"SELECT version FROM {ADK_SCHEMA}.adk_artifact_versions "
                "WHERE agent_id = :agent AND context_id = :context ORDER BY version"
            ),
            {"agent": agent_id, "context": context_id},
        )
        return [row.version for row in rows]


async def test_two_servers_saving_one_artifact_at_once_give_distinct_continuous_versions(db, services) -> None:
    agent_id, context_id = f"agent-{uuid.uuid4()}", str(uuid.uuid4())
    first, second = services(), services()

    async def save_all(service: A2AArtifactService, server: str) -> dict[int, str]:
        saved = {}
        for index in range(SAVES):
            body = f"{server} {index}"
            saved[await _save(service, agent_id, context_id, body)] = body
            await asyncio.sleep(0)
        return saved

    by_first, by_second = await asyncio.gather(save_all(first, "first"), save_all(second, "second"))

    assert not by_first.keys() & by_second.keys()
    assert sorted(by_first.keys() | by_second.keys()) == list(range(2 * SAVES))
    assert await _recorded(db, agent_id, context_id) == list(range(2 * SAVES))

    for version, body in {**by_first, **by_second}.items():
        await _store_version(agent_id, context_id, version, body)
    await _turn(agent_id, context_id)
    reader = services()
    assert await reader.list_versions(
        app_name=agent_id, user_id=USER, session_id=context_id, filename=FILENAME
    ) == list(range(2 * SAVES))
    for version, body in {**by_first, **by_second}.items():
        loaded = await reader.load_artifact(
            app_name=agent_id, user_id=USER, session_id=context_id, filename=FILENAME, version=version
        )
        assert loaded.text == body
    for service, saved in ((first, by_first), (second, by_second)):
        for version, body in saved.items():
            loaded = await service.load_artifact(
                app_name=agent_id, user_id=USER, session_id=context_id, filename=FILENAME, version=version
            )
            assert loaded.text == body


async def test_a_server_numbers_after_versions_given_but_not_yet_stored(db, services) -> None:
    """The tasks carrying another server's versions may not be written yet; the numbers are."""
    agent_id, context_id = f"agent-{uuid.uuid4()}", str(uuid.uuid4())
    first = services()
    assert [await _save(first, agent_id, context_id, f"v{index}") for index in range(3)] == [0, 1, 2]

    assert await _save(services(), agent_id, context_id, "elsewhere") == 3


async def test_deleting_the_contexts_adk_state_forgets_its_versions(db, services) -> None:
    """``DeleteContext`` deletes the framework state first: there the version numbers go, before the tasks."""
    agent_id, context_id, kept_id = f"agent-{uuid.uuid4()}", str(uuid.uuid4()), str(uuid.uuid4())
    service = services()
    for context in (context_id, kept_id):
        await _save(service, agent_id, context, "first")
        await _store_version(agent_id, context, 0, "first")
    executor = ADKExecutor(
        BaseAgent(name="agent"), Mock(), session_service=InMemorySessionService(), artifact_service=service,
        memory_service=InMemoryMemoryService(),
    )

    await executor.delete_state(
        ExecutionConfig(context_id=context_id, state_scope=StateScope(agent_id=agent_id, owner_scope=USER))
    )

    assert await _recorded(db, agent_id, context_id) == []
    assert await _recorded(db, agent_id, kept_id) == [0]


async def test_the_numbers_given_in_the_sdks_own_schema_are_copied_into_the_adk_schema(db) -> None:
    """The table moved from the SDK's schema; a number given there is not given again."""
    agent_id, schema = f"agent-{uuid.uuid4()}", f"adk_copy_{uuid.uuid4().hex[:10]}"
    async with db.get_engine().begin() as conn:
        await conn.execute(
            text(
                f"INSERT INTO {AION_SCHEMA}.adk_artifact_versions "
                "(agent_id, owner_scope, context_id, artifact_name, version) VALUES (:a, '', 'ctx', 'report.txt', 4)"
            ),
            {"a": agent_id},
        )
    try:
        await migrate_aion_tables(db, schema=schema)
        async with db.get_engine().connect() as conn:
            copied = (await conn.execute(
                text(f"SELECT version FROM {schema}.adk_artifact_versions WHERE agent_id = :a"), {"a": agent_id}
            )).scalars().all()
        assert copied == [4]
    finally:
        async with db.get_engine().begin() as conn:
            await conn.execute(text(f"DELETE FROM {AION_SCHEMA}.adk_artifact_versions WHERE agent_id = :a"), {"a": agent_id})
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))

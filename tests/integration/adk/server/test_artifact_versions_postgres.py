"""ADK artifact versions numbered by several servers of one agent over one PostgreSQL.

Each ``A2AArtifactService`` here is a server of its own: its memory is its
own, and only the database is shared. A version's artifact reaches the tasks
table later, with the task that carries it, as the event consumer writes it;
the tests write it the same way once the saves are done.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import pytest_asyncio
from a2a.server.context import ServerCallContext
from a2a.types import Artifact, Part, Task, TaskState, TaskStatus
from google.genai import types
from sqlalchemy import text

from tests.integration.server.tasks.postgres_support import POSTGRES_TEST_URL, prepared_database

from aion.adk.server.artifacts.backends.a2a import A2AArtifactService
from aion.server.tasks.admission import ContextHolder, PostgresContextAdmission
from aion.server.tasks.contexts import DeletionStep, FinishOutcome, PostgresContextCatalog
from aion.server.tasks.stores.postgres_task_store import PostgresTaskStore

pytestmark = [
    pytest.mark.skipif(not POSTGRES_TEST_URL, reason="POSTGRES_TEST_URL is not set"),
    pytest.mark.asyncio(loop_scope="module"),
]

USER = ""
"""The anonymous owner, the one ``ServerCallContext()`` writes tasks as."""

FILENAME = "report.txt"
SAVES = 6
"""Saves per server: enough for the two to meet on the same numbers every run."""


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def db():
    async with prepared_database() as manager:
        yield manager


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
    artifact = Artifact(artifact_id=uuid.uuid4().hex, name=FILENAME, parts=[Part(text=text_)])
    artifact.metadata["version"] = str(version)
    await PostgresTaskStore(agent_id=agent_id).save(
        Task(
            id=str(uuid.uuid4()),
            context_id=context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            artifacts=[artifact],
        ),
        ServerCallContext(),
    )


async def _recorded(db, agent_id: str, context_id: str) -> list[int]:
    async with db.get_session() as session:
        rows = await session.execute(
            text(
                "SELECT version FROM adk_artifact_versions "
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


async def test_deleting_the_context_forgets_its_versions(db, services) -> None:
    agent_id, context_id, kept_id = f"agent-{uuid.uuid4()}", str(uuid.uuid4()), str(uuid.uuid4())
    admission = PostgresContextAdmission(agent_id, db)
    catalog = PostgresContextCatalog(agent_id, db)
    for context in (context_id, kept_id):
        assert await admission.admit(context, ContextHolder.private("alice"), "alice")
        await _save(services(), agent_id, context, "first")
        await _store_version(agent_id, context, 0, "first")

    ticket = await catalog.begin_deletion(context_id, "alice")
    assert ticket.step == DeletionStep.STARTED
    outcome, _ = await catalog.finish_deletion(context_id, ticket.operation_id)

    assert outcome == FinishOutcome.FINISHED
    assert await _recorded(db, agent_id, context_id) == []
    assert await _recorded(db, agent_id, kept_id) == [0]

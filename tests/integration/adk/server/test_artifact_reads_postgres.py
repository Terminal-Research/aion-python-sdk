"""The ADK artifact service reads a context's tasks over PostgreSQL as a turn's history does.

Each test stores a context's tasks with a real task store and context
reservations, installs the history the server gives a turn
(``aion.core.agent.conversation``) and asks the artifact service, with the
server's database, as ADK asks it: ``app_name`` the agent, ``user_id`` the
conversation's state owner, ``session_id`` the context.

* The participants of a shared conversation find each other's artifacts:
  the tasks belong to each participant, the state owner is the
  conversation's, and every task of the context is read.
* A private context's artifacts are its holder's alone: another user
  presenting the same ``context_id``, or another agent, finds nothing.
"""

from __future__ import annotations

import uuid
from typing import Optional

import pytest
import pytest_asyncio
from google.genai import types

from tests.integration.adk.server.postgres_turns import PostgresAgent, participant, private, runtime_provider
from tests.integration.server.tasks.postgres_support import POSTGRES_TEST_URL, prepared_database

from aion.adk.server.artifacts.backends.a2a import A2AArtifactService

pytestmark = [
    pytest.mark.skipif(not POSTGRES_TEST_URL, reason="POSTGRES_TEST_URL is not set"),
    pytest.mark.asyncio(loop_scope="module"),
]

FILENAME = "report.txt"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def db():
    async with prepared_database() as manager:
        yield manager


@pytest.fixture(autouse=True)
def _runtime_provider():
    with runtime_provider():
        yield


@pytest_asyncio.fixture(loop_scope="module")
async def service(db):
    artifact_service = A2AArtifactService(db_manager=db)
    yield artifact_service
    await artifact_service.close()


async def _load(
        service: A2AArtifactService, agent: PostgresAgent, user_id: str, context_id: str, version: Optional[int] = None
) -> Optional[str]:
    part = await service.load_artifact(
        app_name=agent.id, user_id=user_id, session_id=context_id, filename=FILENAME, version=version
    )
    return part.text if part is not None else None


async def test_a_participant_finds_the_artifacts_another_participant_saved(service) -> None:
    agent, context_id = PostgresAgent(), str(uuid.uuid4())
    alice, bob = participant("alice"), participant("bob")
    assert await agent.admit(context_id, alice) and await agent.admit(context_id, bob)
    await agent.store_artifact(context_id, alice, FILENAME, 0, "alice v0")
    await agent.store_artifact(context_id, alice, FILENAME, 1, "alice v1")

    user_id = agent.turn(context_id, bob)

    assert await _load(service, agent, user_id, context_id) == "alice v1"
    assert await _load(service, agent, user_id, context_id, 0) == "alice v0"
    assert await service.list_artifact_keys(app_name=agent.id, user_id=user_id, session_id=context_id) == [FILENAME]
    assert await service.list_versions(
        app_name=agent.id, user_id=user_id, session_id=context_id, filename=FILENAME
    ) == [0, 1]
    assert await service.save_artifact(
        app_name=agent.id, user_id=user_id, session_id=context_id, filename=FILENAME, artifact=types.Part(text="bob")
    ) == 2


async def test_a_private_contexts_artifacts_are_its_holders_alone(service) -> None:
    agent, other_agent, context_id = PostgresAgent(), PostgresAgent(), str(uuid.uuid4())
    alice, mallory = private("alice"), private("mallory")
    assert await agent.admit(context_id, alice)
    await agent.store_artifact(context_id, alice, FILENAME, 0, "alice v0")
    await agent.store_artifact(context_id, alice, FILENAME, 1, "alice v1")

    assert await _load(service, agent, agent.turn(context_id, alice), context_id) == "alice v1"

    assert not await agent.admit(context_id, mallory)
    user_id = agent.turn(context_id, mallory)
    assert await _load(service, agent, user_id, context_id) is None
    assert await service.list_artifact_keys(app_name=agent.id, user_id=user_id, session_id=context_id) == []
    assert await service.list_versions(
        app_name=agent.id, user_id=user_id, session_id=context_id, filename=FILENAME
    ) == []

    assert await other_agent.admit(context_id, alice)
    assert await _load(service, other_agent, other_agent.turn(context_id, alice), context_id) is None

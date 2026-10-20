"""An executor's artifacts numbered and found by context, held alike by the in-memory and PostgreSQL backends.

Each test runs the scripted executor, with the turn's history
(``aion.core.agent.conversation``) over a real task store, context
reservations and artifact reader of one kind, and saves the events a turn
yields as the server's pipeline does:

* the tasks of a context write one artifact id as versions 0, 1, 2, and the
  lookup answers the latest;
* a caller that does not hold the context numbers from 0 and finds nothing,
  even for a ``context_id`` another holder uses;
* a shared context's tasks are every participant's, whoever wrote them.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from a2a.auth.user import User
from a2a.server.agent_execution import RequestContext
from a2a.server.cluster import LegacyTaskStoreAdapter, TaskVersion
from a2a.server.context import ServerCallContext
from a2a.server.owner_resolver import resolve_user_scope
from a2a.server.tasks.task_manager import append_artifact_to_task
from a2a.types import (
    Message as A2AMessage,
    Part as A2APart,
    Role,
    SendMessageRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
)

from .postgres_support import POSTGRES_TEST_URL, prepared_database, stores as postgres_stores, truncate

from aion.core.principal import Principal
from aion.db.postgres.manager import db_manager
from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from aion.server.agent.adapters import ExecutionConfig
from aion.server.agent.execution.context.providers import RequestScopeRuntimeContextProvider
from aion.server.auth import (
    Assurance,
    AuthenticatedCaller,
    CallerCredentials,
    CredentialKind,
    GatewayCoordinates,
)
from aion.server.tasks.admission import ContextHolder, InMemoryContextAdmission, PostgresContextAdmission
from aion.server.tasks.context_artifacts import InMemoryContextArtifacts, PostgresContextArtifacts
from aion.server.tasks.context_runs import InMemoryContextRuns, PostgresContextRuns
from aion.server.tasks.contexts import InMemoryContextCatalog
from aion.server.tasks.conversation import StoredConversations
from aion.server.tasks.stores.in_memory_task_store import InMemoryTaskStore

from tests.support.conversation import install
from tests.support.scripted_executor.executor import ScriptedExecutor
from tests.support.scripted_executor.script import COMPLETED, Script

pytestmark = [pytest.mark.asyncio(loop_scope="module")]

SPEC = "evolution-spec"
GATEWAY = ContextHolder.shared("8e7d6c5b-4a39-4281-b7c6-d5e4f3a2b1c0", "2b4c6d8e-0f1a-4b3c-8d5e-7f9a1b2c3d4e")


class _User(User):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._name


def _caller(name: str, *, shared: bool = False) -> ServerCallContext:
    """A call context for ``name``; a shared one is an invocation through the gateway conversation."""
    if not shared:
        return ServerCallContext(user=_User(name))
    caller = AuthenticatedCaller(
        Principal("ExternalAnonymous", name),
        credential=CredentialKind.INVOCATION,
        assurance=Assurance.ACCOUNT,
        issuer="aion.io",
        gateway=GatewayCoordinates(GATEWAY.owner_agent_identity_id, GATEWAY.edge_agent_environment_id, "terminal"),
        claims={},
    )
    return ServerCallContext(user=_User(name), state={"auth": CallerCredentials(caller)})


def _spec(text: str) -> dict:
    return {"kind": "artifact", "id": SPEC, "text": text, "metadata": {"specPath": "spec.md"}}


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _database():
    if not POSTGRES_TEST_URL:
        yield None
        return
    async with prepared_database() as manager:
        yield manager


@pytest.fixture(autouse=True)
def _runtime_provider():
    provider = RequestScopeRuntimeContextProvider()
    AionRuntimeContextRegistry.set_provider(provider)
    provider.set_current_context(None)
    yield
    provider.set_current_context(None)
    AionRuntimeContextRegistry.clear_provider()


class _Backend:
    """One backend's store, admission and readers, the history of a turn over them, and an extension's executor."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        if kind == "memory":
            self.store = InMemoryTaskStore(resolve_user_scope)
            self.versioned = LegacyTaskStoreAdapter(self.store)
            self.admission = InMemoryContextAdmission()
            InMemoryContextCatalog(self.store, self.admission)
            self.ledger = InMemoryContextArtifacts(self.store, self.admission)
            runs = InMemoryContextRuns(self.store, self.admission)
        else:
            agent_id = f"agent-{uuid.uuid4()}"
            self.store, self.versioned = postgres_stores(agent_id)
            self.admission = PostgresContextAdmission(agent_id, db_manager)
            self.ledger = PostgresContextArtifacts(agent_id, db_manager)
            runs = PostgresContextRuns(agent_id, db_manager)
        self.conversations = StoredConversations(
            task_store=self.store, context_runs=runs, context_artifacts=self.ledger, owner_resolver=resolve_user_scope
        )
        self.executor = ScriptedExecutor()

    async def write(
            self, context_id: str, name: str, *outputs: Any, holder: ContextHolder | None = None
    ) -> tuple[Task, Any]:
        """Run a new task of ``name`` in the context, save it, and return it with the history its turn had."""
        shared = holder is not None and holder.kind == "shared"
        await self.admission.admit(context_id, holder or ContextHolder.private(name), name)
        self.executor.script = Script(outputs=list(outputs) or [COMPLETED])
        task = Task(
            id=str(uuid.uuid4()), context_id=context_id, status=TaskStatus(state=TaskState.TASK_STATE_WORKING)
        )
        message = A2AMessage(message_id="m-user", role=Role.ROLE_USER, parts=[A2APart(text="go")])
        request = RequestContext(
            call_context=_caller(name, shared=shared),
            request=SendMessageRequest(message=message),
            task_id=task.id,
            context_id=context_id,
            task=task,
        )
        config = ExecutionConfig(task_id=task.id, context_id=context_id)
        history = self.conversations.for_turn(request.call_context, task)
        install(history)
        events = [event async for event in self.executor.stream(request, config)]
        for event in events:
            if isinstance(event, TaskArtifactUpdateEvent):
                append_artifact_to_task(task, event)
        task.status.state = TaskState.TASK_STATE_COMPLETED
        await self.versioned.save(
            task, event=task, prev=None, prev_version=TaskVersion.MISSING, context=_caller(name, shared=shared)
        )
        return task, history

    async def spec(self, context_id: str, name: str, text: str, **kwargs) -> Task:
        task, _ = await self.write(context_id, name, _spec(text), COMPLETED, **kwargs)
        return task

    async def stored_version(self, task: Task) -> str:
        stored = await self.store.get(task.id, _caller("alice"))
        return stored.artifacts[0].metadata["version"]


@pytest_asyncio.fixture(params=["memory", "postgres"], loop_scope="module")
async def backend(request, _database):
    if request.param == "postgres":
        if _database is None:
            pytest.skip("POSTGRES_TEST_URL is not set")
        await truncate()
    return _Backend(request.param)


async def test_three_tasks_write_versions_0_1_2_and_the_latest_is_found(backend):
    first = await backend.spec("ctx", "alice", "one")
    second = await backend.spec("ctx", "alice", "two")
    third = await backend.spec("ctx", "alice", "three")

    assert [await backend.stored_version(task) for task in (first, second, third)] == ["0", "1", "2"]

    _, turn = await backend.write("ctx", "alice")
    found = await turn.find_artifact(SPEC)

    assert (found.task_id, found.version) == (third.id, 2)
    assert found.artifact.artifact_id == SPEC
    assert dict(found.artifact.metadata) == {"specPath": "spec.md", "version": "2"}
    assert [part.text for part in found.artifact.parts] == ["three"]
    assert found.updated_at is not None


async def test_another_context_counts_on_its_own(backend):
    await backend.spec("ctx", "alice", "one")
    await backend.spec("ctx", "alice", "two")

    elsewhere = await backend.spec("elsewhere", "alice", "first here")

    assert await backend.stored_version(elsewhere) == "0"


async def test_a_holder_that_does_not_hold_the_context_numbers_from_zero_and_finds_nothing(backend):
    await backend.spec("ctx", "alice", "alice's plan")

    assert await backend.ledger.versions("ctx", SPEC, ContextHolder.private("bob")) == []
    assert await backend.ledger.versions("ctx", SPEC, GATEWAY) == []
    assert len(await backend.ledger.versions("ctx", SPEC, ContextHolder.private("alice"))) == 1

    stranger, turn = await backend.write("ctx", "bob", _spec("bob's plan"), COMPLETED)

    assert stranger.artifacts[0].metadata["version"] == "0"
    assert await turn.find_artifact(SPEC) is None


async def test_the_tasks_of_a_shared_context_are_every_participants(backend):
    await backend.spec("ctx", "alice", "from alice", holder=GATEWAY)
    from_bob = await backend.spec("ctx", "bob", "from bob", holder=GATEWAY)

    versions = await backend.ledger.versions("ctx", SPEC, GATEWAY)

    assert [(item.task_id, item.version) for item in versions] == [(from_bob.id, 1), (versions[1].task_id, 0)]
    assert from_bob.artifacts[0].metadata["version"] == "1"

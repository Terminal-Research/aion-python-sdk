"""Request metadata and the stored task, on PostgreSQL.

A request's ``params.metadata`` carries the daemon payload, and with it the
environment's configuration variables in plain text. The task a request starts
is stored in ``tasks``, its every write is journaled in ``task_events``, which
nothing prunes, and both are read back by whoever reads the task. So the
request's metadata stays on the request: the stored task holds only what the
server and the extension handler wrote.

``strip_request_metadata`` is the clean-up behind ``aion db
strip-request-metadata``: it removes inbound extension payloads from the tasks
and journal snapshots a database already holds, and leaves every other key.
"""

from __future__ import annotations

import json
import uuid

import pytest
import pytest_asyncio
from a2a.auth.user import User
from a2a.server.cluster import DatabaseTaskEventStream
from a2a.server.cluster.version import TaskVersion
from a2a.server.context import ServerCallContext
from a2a.types import (
    GetTaskRequest,
    Message,
    Part,
    Role,
    SendMessageRequest,
    StreamResponse,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from google.protobuf.json_format import MessageToDict, MessageToJson
from sqlalchemy import text
from unittest.mock import Mock

from aion.core.a2a.enums import A2AMetadataKey
from aion.core.constants.a2a import (
    BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1,
    DAEMON_EXTENSION_URI_V1,
    DISTRIBUTION_EXTENSION_URI_V1,
)
from aion.core.runtime import aion_a2a_extension_registry
from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from aion.server.agent.execution import AionAgentRequestExecutor
from aion.server.agent.execution.context.providers import RequestScopeRuntimeContextProvider
from aion.server.agent.execution.request_context_builder import AionRequestContextBuilder
from aion.server.agent.execution.scope import init_execution_scope
from aion.server.core.app.handlers.request_handler import AionRequestHandler
from aion.server.tasks.admission import PostgresContextAdmission
from aion.server.tasks.contexts import PostgresContextCatalog
from aion.server.tasks import request_metadata
from aion.server.tasks.request_metadata import strip_request_metadata

from .postgres_support import POSTGRES_TEST_URL, prepared_database, stores, truncate
from .postgres_support import db_manager as _db_manager

pytestmark = [
    pytest.mark.skipif(not POSTGRES_TEST_URL, reason="POSTGRES_TEST_URL is not set"),
    pytest.mark.asyncio(loop_scope="module"),
]

SECRET = "sk-configuration-secret"
"""A configuration variable's value, as the daemon payload carries it."""

EVOLUTION = BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1
ROUTED = A2AMetadataKey.ROUTED_EXTENSION.value
SETTLED = A2AMetadataKey.SETTLED_REASON.value
PROGRESS = {"stage": "implement"}
"""What the extension handler writes into its task, under its own URI."""


class _User(User):
    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return "alice"


ALICE = ServerCallContext(user=_User())


def _daemon() -> dict:
    """The daemon extension payload as the platform sends it."""
    return {
        "daemonIdentity": {"kind": "daemon", "id": "daemon-1", "networkType": "Aion", "organizationId": "org-1"},
        "behavior": {"id": "b-1", "behaviorKey": "main", "versionId": "v-1"},
        "environment": {
            "id": "env-1",
            "name": "prod",
            "projectId": "proj-1",
            "deploymentId": "dep-1",
            "configurationVariables": {"MODEL_API_KEY": SECRET},
            "daemonAgentIdentityId": "daemon-1",
        },
    }


def _completed(context, metadata: dict | None = None) -> TaskStatusUpdateEvent:
    return TaskStatusUpdateEvent(
        task_id=context.task_id,
        context_id=context.context_id,
        status=TaskStatus(
            state=TaskState.TASK_STATE_COMPLETED,
            message=Message(message_id=uuid.uuid4().hex, role=Role.ROLE_AGENT, parts=[Part(text="done")]),
        ),
        metadata=metadata,
    )


class _Agent:
    """The agent's framework adapter: answers and completes."""

    supports_context_state_deletion = False

    async def stream(self, context):
        yield _completed(context)


class _EvolutionHandler:
    """An extension handler that records its progress in the task and completes."""

    uri = EVOLUTION

    async def stream(self, context):
        yield _completed(context, metadata={EVOLUTION: PROGRESS})


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _database():
    async with prepared_database() as manager:
        yield manager


@pytest.fixture
def runtime_context():
    AionRuntimeContextRegistry.set_provider(RequestScopeRuntimeContextProvider())
    yield
    AionRuntimeContextRegistry.clear_provider()


@pytest.fixture
def evolution_enabled():
    aion_a2a_extension_registry.activate([DAEMON_EXTENSION_URI_V1, EVOLUTION])
    yield
    aion_a2a_extension_registry.reset_to_default()


@pytest_asyncio.fixture(loop_scope="module")
async def handler(_database, runtime_context, evolution_enabled):
    """One server of the agent, assembled as ``AppFactory`` assembles it on PostgreSQL."""
    await truncate()
    store, versioned = stores("metadata-agent")
    card = Mock()
    card.capabilities.streaming = True
    built = AionRequestHandler(
        agent_executor=AionAgentRequestExecutor(_Agent(), extension_handlers=[_EvolutionHandler()]),
        task_store=versioned,
        agent_card=card,
        event_stream=DatabaseTaskEventStream(_db_manager.get_engine(), create_table=False, poll_interval_s=0.05),
        admission=PostgresContextAdmission(store.agent_id, _db_manager),
        context_catalog=PostgresContextCatalog(store.agent_id, _db_manager),
        request_context_builder=AionRequestContextBuilder(task_store=store),
    )
    try:
        yield built
    finally:
        await built._active_task_registry.aclose()


async def _send(handler: AionRequestHandler, *, extensions: tuple[str, ...] = ()) -> Task:
    """Send a daemon-driven request that also plants server keys; the stream's closing Task."""
    init_execution_scope()
    message = Message(message_id=uuid.uuid4().hex, role=Role.ROLE_USER, parts=[Part(text="improve")])
    message.extensions.extend(extensions)
    request = SendMessageRequest(message=message)
    request.metadata.update({
        DAEMON_EXTENSION_URI_V1: _daemon(),
        SETTLED: "server_shutdown",
        ROUTED: EVOLUTION,
        "client-note": "for this turn",
    })
    events = [event async for event in handler.on_message_send_stream(request, ALICE)]
    closing = events[-1]
    assert isinstance(closing, Task) and closing.status.state == TaskState.TASK_STATE_COMPLETED
    return closing


async def _stored_metadata(task_id: str) -> dict | None:
    async with _db_manager.get_session() as session:
        result = await session.execute(
            text("SELECT metadata FROM tasks WHERE id = :id"), {"id": uuid.UUID(task_id)}
        )
        return result.scalar_one()


async def _journal(task_id: str | None = None) -> list[bytes]:
    """The ``event_data`` of every journal row, of one task or of all, in order."""
    query = "SELECT event_data FROM task_events"
    if task_id is not None:
        query += " WHERE task_id = :id"
    async with _db_manager.get_session() as session:
        result = await session.execute(text(query + " ORDER BY seq"), {"id": task_id})
        return [bytes(row[0]) for row in result.all()]


def _snapshots(journal: list[bytes]) -> list[Task]:
    """The Task snapshots among journal rows."""
    snapshots = []
    for data in journal:
        event = StreamResponse()
        event.ParseFromString(data)
        if event.HasField("task"):
            snapshots.append(event.task)
    return snapshots


# --------------------------------------------------------------------------
# A request's metadata does not reach the task
# --------------------------------------------------------------------------

async def test_an_extension_task_holds_only_what_the_server_and_its_handler_wrote(handler) -> None:
    closing = await _send(handler, extensions=(EVOLUTION,))

    assert await _stored_metadata(closing.id) == {ROUTED: EVOLUTION, EVOLUTION: PROGRESS}
    journal = await _journal(closing.id)
    assert journal, "every write of the task is journaled"
    assert not any(SECRET.encode() in data for data in journal)
    assert all(
        set(MessageToDict(snapshot.metadata)) <= {ROUTED, EVOLUTION}
        for snapshot in _snapshots(journal)
    )
    read = await handler.on_get_task(GetTaskRequest(id=closing.id), ALICE)
    assert MessageToDict(read.metadata) == {ROUTED: EVOLUTION, EVOLUTION: PROGRESS}
    assert SECRET not in MessageToJson(read) and SECRET not in MessageToJson(closing)


async def test_a_task_of_the_agent_holds_no_metadata_and_is_routed_nowhere(handler) -> None:
    """A route named in the request is not a route: only the server decides one."""
    closing = await _send(handler)

    assert await _stored_metadata(closing.id) in (None, {})
    journal = await _journal(closing.id)
    assert not any(SECRET.encode() in data or SETTLED.encode() in data for data in journal)
    read = await handler.on_get_task(GetTaskRequest(id=closing.id), ALICE)
    assert MessageToDict(read.metadata) == {}


# --------------------------------------------------------------------------
# Cleaning up a database that already holds request metadata
# --------------------------------------------------------------------------

async def _store_with_metadata(metadata: dict) -> str:
    """Write a task holding ``metadata``, journaled as its Task snapshot, then a status update.

    The status update names the daemon extension in its message's extensions,
    so its bytes contain a request key although it holds no metadata at all.
    """
    _, versioned = stores("metadata-agent")
    task = Task(
        id=str(uuid.uuid4()),
        context_id=str(uuid.uuid4()),
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
    )
    task.metadata.update(metadata)
    version = await versioned.save(task, event=task, prev_version=TaskVersion.MISSING, context=ALICE)

    update = TaskStatusUpdateEvent(
        task_id=task.id,
        context_id=task.context_id,
        status=TaskStatus(
            state=TaskState.TASK_STATE_WORKING,
            message=Message(
                message_id=uuid.uuid4().hex,
                role=Role.ROLE_AGENT,
                parts=[Part(text="working")],
                extensions=[DAEMON_EXTENSION_URI_V1],
            ),
        ),
    )
    task.status.CopyFrom(update.status)
    await versioned.save(task, event=update, prev_version=version, context=ALICE)
    return task.id


@pytest_asyncio.fixture(loop_scope="module")
async def stored(_database):
    """Two tasks as a database may hold them: one with server keys beside the request's, one with only the request's."""
    await truncate()
    mixed = await _store_with_metadata({
        DAEMON_EXTENSION_URI_V1: _daemon(),
        DISTRIBUTION_EXTENSION_URI_V1: {"senderId": "aion:user:1"},
        ROUTED: EVOLUTION,
        SETTLED: "server_shutdown",
        EVOLUTION: PROGRESS,
        "agent-key": "kept",
    })
    request_only = await _store_with_metadata({DAEMON_EXTENSION_URI_V1: _daemon()})
    return mixed, request_only


def _counts(findings) -> dict[str, tuple[int, int]]:
    return {finding.table: (finding.rows, finding.keys) for finding in findings}


async def test_a_dry_run_counts_the_request_keys_and_changes_nothing(stored) -> None:
    before = (await _journal(), [await _stored_metadata(task_id) for task_id in stored])

    findings = await strip_request_metadata(_db_manager, apply=False)

    assert _counts(findings) == {"tasks": (2, 3), "task_events": (2, 3)}
    assert (await _journal(), [await _stored_metadata(task_id) for task_id in stored]) == before


async def test_apply_removes_the_request_keys_and_keeps_every_other(stored, monkeypatch) -> None:
    """One journal row per batch, so every row is reached across batches."""
    mixed, request_only = stored
    status_updates_before = [data for data in await _journal() if not _snapshots([data])]
    monkeypatch.setattr(request_metadata, "_EVENT_BATCH_SIZE", 1)

    findings = await strip_request_metadata(_db_manager, apply=True)

    assert _counts(findings) == {"tasks": (2, 3), "task_events": (2, 3)}
    server_keys = {ROUTED: EVOLUTION, SETTLED: "server_shutdown", EVOLUTION: PROGRESS, "agent-key": "kept"}
    assert await _stored_metadata(mixed) == server_keys
    assert await _stored_metadata(request_only) is None
    journal = await _journal()
    assert not any(SECRET.encode() in data for data in journal)
    assert [MessageToDict(snapshot.metadata) for snapshot in _snapshots(await _journal(mixed))] == [server_keys]
    assert [snapshot.HasField("metadata") for snapshot in _snapshots(await _journal(request_only))] == [False]
    assert [data for data in journal if not _snapshots([data])] == status_updates_before

    store, _ = stores("metadata-agent")
    read = await store.get(request_only, ALICE)
    assert MessageToDict(read.metadata) == {}
    assert json.loads(MessageToJson(await store.get(mixed, ALICE)))["metadata"] == server_keys

    again = await strip_request_metadata(_db_manager, apply=True)
    assert _counts(again) == {"tasks": (0, 0), "task_events": (0, 0)}

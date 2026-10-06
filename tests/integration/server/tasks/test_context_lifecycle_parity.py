"""The Context extension's contract, held alike by the in-memory and PostgreSQL backends.

Each test drives ``ContextService`` over a real task store, admission and
catalog of one kind, so the two backends cannot drift apart:

* a context is visible exactly to the callers bound to it, and its history is
  every participant's, chronological, paged newest-relative;
* artifacts are qualified by task, newest first, without streaming deltas;
* ``DeleteContext`` removes the caller's binding; the last one cancels the
  context's active tasks through the store, deletes its framework state under
  the holder's scope and removes its tasks, messages, artifacts, versions and
  bindings, after which the ID starts a new conversation;
* an unsettled deletion stays durable and fenced, and the same caller's retry
  finishes it; a late write of a removed task is refused.

Cancellation is a2a-sdk's remote cancellation - ``CANCELED`` written over the
stored version, unscoped - the path a task with no live execution takes; and
framework state goes through a recording fake.
"""

from __future__ import annotations

import uuid
from typing import Optional

import pytest
import pytest_asyncio
from a2a.auth.user import User
from a2a.server.cluster import ConcurrentTaskModificationError, LegacyTaskStoreAdapter, TaskVersion
from a2a.server.context import ServerCallContext
from a2a.server.owner_resolver import resolve_user_scope
from a2a.types import (
    Artifact,
    Message,
    Part,
    Role,
    Task,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from sqlalchemy import text

from .postgres_support import POSTGRES_TEST_URL, prepared_database, stores as postgres_stores, task_version, truncate

from aion.core.a2a import (
    A2AMetadataKey,
    ArtifactId,
    DeleteContextParams,
    GetContextParams,
    GetContextsParams,
    MessageType,
)
from aion.db.postgres.manager import db_manager
from aion.server.contexts import ContextDeletionInProgress, ContextNotFound, ContextService, ContextStateDeleter
from aion.server.tasks.admission import ContextHolder, InMemoryContextAdmission, PostgresContextAdmission
from aion.server.tasks.contexts import InMemoryContextCatalog, PostgresContextCatalog
from aion.server.tasks.push_notifications import PushNotificationFactory
from aion.server.tasks.stores.in_memory_task_store import InMemoryTaskStore

pytestmark = [pytest.mark.asyncio(loop_scope="module")]

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


def _caller(name: str) -> ServerCallContext:
    return ServerCallContext(user=_User(name))


def _message(message_id: str, message_type: Optional[str] = None) -> Message:
    message = Message(message_id=message_id, role=Role.ROLE_USER, parts=[Part(text=message_id)])
    if message_type is not None:
        message.metadata.update({A2AMetadataKey.MESSAGE_TYPE.value: message_type})
    return message


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _database():
    if not POSTGRES_TEST_URL:
        yield None
        return
    async with prepared_database() as manager:
        yield manager


class _Backend:
    """One backend's store, admission and catalog, and a service over them."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.deleted: list[tuple] = []
        self.unsettled = False
        if kind == "memory":
            self.store = InMemoryTaskStore(resolve_user_scope)
            self.versioned = LegacyTaskStoreAdapter(self.store)
            self.admission = InMemoryContextAdmission()
            catalog = InMemoryContextCatalog(self.store, self.admission)
        else:
            agent_id = f"agent-{uuid.uuid4()}"
            self.store, self.versioned = postgres_stores(agent_id)
            self.admission = PostgresContextAdmission(agent_id, db_manager)
            catalog = PostgresContextCatalog(agent_id, db_manager)
        self.service = ContextService(
            catalog=catalog,
            owner_resolver=resolve_user_scope,
            cancel_task=self._cancel,
            state_deleter=ContextStateDeleter(supported=lambda: True, delete=self._delete_state),
        )

    async def _cancel(self, task_id: str):
        if self.unsettled:
            raise TimeoutError("the owner has not answered yet")
        stored = await self.versioned.get(task_id, None)
        if stored is None:
            return None
        cancelled = Task()
        cancelled.CopyFrom(stored.task)
        cancelled.status.state = TaskState.TASK_STATE_CANCELED
        await self.versioned.save(
            cancelled,
            event=TaskStatusUpdateEvent(task_id=task_id, context_id=cancelled.context_id, status=cancelled.status),
            prev=stored.task,
            prev_version=stored.version,
            context=None,
        )
        return cancelled

    async def _delete_state(self, context_id, *, owner_scope, gateway):
        self.deleted.append((context_id, owner_scope, gateway))

    async def save(self, task: Task, name: str) -> None:
        """Write a new task as a server does: through the versioned store, with its event."""
        await self.versioned.save(
            task, event=task, prev=None, prev_version=TaskVersion.MISSING, context=_caller(name)
        )

    async def send(
        self,
        name: str,
        context_id: str,
        *messages: Message,
        holder: Optional[ContextHolder] = None,
        state=TaskState.TASK_STATE_COMPLETED,
        artifacts: tuple[Artifact, ...] = (),
    ) -> Task:
        assert await self.admission.admit(context_id, holder or ContextHolder.private(name), name)
        task = Task(
            id=str(uuid.uuid4()),
            context_id=context_id,
            status=TaskStatus(state=state),
            history=list(messages),
            artifacts=list(artifacts),
        )
        await self.save(task, name)
        return task

    async def contexts(self, name: str, **params) -> list[str]:
        listing = await self.service.get_contexts(GetContextsParams(**params), _caller(name))
        return [summary.context_id for summary in listing.root]

    async def history(self, name: str, context_id: str, **params) -> list[str]:
        view = await self.service.get_context(GetContextParams(context_id=context_id, **params), _caller(name))
        return [message.message_id for message in view.history]

    async def delete(self, name: str, context_id: str) -> None:
        await self.service.delete_context(DeleteContextParams(context_id=context_id), _caller(name))

    async def task_count(self, context_id: str) -> int:
        return len(await self.store.get_context_tasks(context_id, limit=100))


@pytest_asyncio.fixture(params=["memory", "postgres"], loop_scope="module")
async def backend(request, _database):
    if request.param == "postgres":
        if _database is None:
            pytest.skip("POSTGRES_TEST_URL is not set")
        await truncate()
    return _Backend(request.param)


async def test_contexts_are_listed_by_latest_activity(backend):
    await backend.send("alice", "old", _message("m1"))
    await backend.send("alice", "new", _message("m2"))
    await backend.send("alice", "old", _message("m3"))
    await backend.send("bob", "bobs", _message("m4"))

    assert await backend.contexts("alice") == ["old", "new"]
    assert await backend.contexts("alice", history_length=1, history_offset=1) == ["new"]
    assert await backend.contexts("bob") == ["bobs"]


async def test_history_is_chronological_paged_and_conversational(backend):
    await backend.send("alice", "ctx", _message("m1"), _message("event", MessageType.EVENT.value), _message("m2"))
    await backend.send("alice", "ctx", _message("m3"), _message("m4", MessageType.MESSAGE.value), _message("m5"))

    assert await backend.history("alice", "ctx") == ["m1", "m2", "m3", "m4", "m5"]
    assert await backend.history("alice", "ctx", history_length=2, history_offset=1) == ["m3", "m4"]
    assert await backend.history("alice", "ctx", history_length=0) == []


async def test_artifacts_are_qualified_newest_first_without_deltas(backend):
    first = await backend.send("alice", "ctx", artifacts=(Artifact(artifact_id="report"),))
    second = await backend.send(
        "alice",
        "ctx",
        artifacts=(Artifact(artifact_id="report"), Artifact(artifact_id=ArtifactId.STREAM_DELTA.value)),
    )

    view = await backend.service.get_context(GetContextParams(context_id="ctx"), _caller("alice"))

    assert [(item.task_id, item.artifact.artifact_id) for item in view.artifacts] == [
        (second.id, "report"),
        (first.id, "report"),
    ]
    assert view.status.state == TaskState.TASK_STATE_COMPLETED


async def test_a_context_is_visible_only_to_its_bound_callers(backend):
    await backend.send("alice", "ctx", _message("m1"))

    with pytest.raises(ContextNotFound):
        await backend.history("bob", "ctx")
    await backend.delete("bob", "ctx")

    assert await backend.history("alice", "ctx") == ["m1"]


async def test_a_shared_context_lives_until_its_last_participant_deletes_it(backend):
    await backend.send("alice", "ctx", _message("from-alice"), holder=GATEWAY)
    await backend.send("bob", "ctx", _message("from-bob"), holder=GATEWAY)
    assert await backend.history("alice", "ctx") == ["from-alice", "from-bob"]

    await backend.delete("alice", "ctx")

    assert await backend.contexts("alice") == []
    assert await backend.history("bob", "ctx") == ["from-alice", "from-bob"]
    assert backend.deleted == []

    await backend.delete("bob", "ctx")

    assert await backend.task_count("ctx") == 0
    assert backend.deleted == [("ctx", None, (GATEWAY.owner_agent_identity_id, GATEWAY.edge_agent_environment_id))]


async def test_deleting_cancels_active_tasks_and_removes_everything(backend):
    await backend.send("alice", "ctx", _message("m1"))
    working = await backend.send("alice", "ctx", _message("m2"), state=TaskState.TASK_STATE_WORKING)

    await backend.delete("alice", "ctx")

    assert await backend.task_count("ctx") == 0
    assert await backend.store.get(working.id, None) is None
    assert backend.deleted == [("ctx", "alice", None)]
    with pytest.raises(ContextNotFound):
        await backend.history("alice", "ctx")


async def test_an_unsettled_deletion_is_fenced_and_resumed_by_its_caller(backend):
    working = await backend.send("alice", "ctx", _message("m1"), state=TaskState.TASK_STATE_WORKING)
    backend.unsettled = True

    with pytest.raises(ContextDeletionInProgress) as first:
        await backend.delete("alice", "ctx")

    assert not await backend.admission.admit("ctx", ContextHolder.private("alice"), "alice")
    await backend.delete("bob", "ctx")
    assert await backend.task_count("ctx") == 1

    with pytest.raises(ContextDeletionInProgress) as second:
        await backend.delete("alice", "ctx")
    assert second.value.deletion_operation_id == first.value.deletion_operation_id

    backend.unsettled = False
    await backend.delete("alice", "ctx")

    assert await backend.task_count("ctx") == 0
    assert await backend.store.get(working.id, None) is None


async def test_a_late_write_cannot_restore_a_removed_task(backend):
    """An execution still writing after the deletion cannot bring the task back.

    The writer holds the version it last read. On PostgreSQL the deletion
    removed the version row, so the write is stale; in memory the removed
    task is retired. And written afresh, as a new task, it is refused by
    admission: the context is no longer active.
    """
    task = await backend.send("alice", "ctx", _message("m1"))
    stored = await backend.versioned.get(task.id, _caller("alice"))
    await backend.delete("alice", "ctx")

    with pytest.raises(ConcurrentTaskModificationError):
        await backend.versioned.save(
            task, event=None, prev=stored.task, prev_version=stored.version, context=_caller("alice")
        )
    with pytest.raises(ConcurrentTaskModificationError):
        await backend.store.save(task, _caller("alice"))

    assert await backend.task_count("ctx") == 0


async def test_a_deleted_context_id_starts_a_new_conversation(backend):
    await backend.send("alice", "ctx", _message("m1"))
    await backend.delete("alice", "ctx")

    await backend.send("bob", "ctx", _message("fresh"))

    assert await backend.history("bob", "ctx") == ["fresh"]
    with pytest.raises(ContextNotFound):
        await backend.history("alice", "ctx")


async def test_postgres_removes_versions_and_push_configs_with_the_tasks(backend):
    if backend.kind != "postgres":
        pytest.skip("storage detail of the PostgreSQL backend")
    task = await backend.send("alice", "ctx", _message("m1"))
    push_configs = PushNotificationFactory._create_postgres_store(db_manager, resolve_user_scope)
    await push_configs.set_info(
        task.id,
        TaskPushNotificationConfig(id="c1", task_id=task.id, url="https://example.invalid/hook"),
        _caller("alice"),
    )
    assert await push_configs.get_info_for_dispatch(task.id)

    assert await task_version(task.id) is not None

    await backend.delete("alice", "ctx")

    assert await task_version(task.id) is None
    async with db_manager.get_session() as session:
        remaining = await session.scalar(
            text("SELECT count(*) FROM push_notification_configs WHERE task_id = :task_id"), {"task_id": task.id}
        )
        bindings = await session.scalar(
            text("SELECT count(*) FROM context_bindings WHERE agent_id = :agent_id"),
            {"agent_id": backend.store.agent_id},
        )
        state = await session.scalar(
            text("SELECT state FROM context_reservations WHERE agent_id = :agent_id AND context_id = 'ctx'"),
            {"agent_id": backend.store.agent_id},
        )
    assert remaining == 0
    assert bindings == 0
    assert state == "deleted"


async def test_a_deleted_blocked_context_stays_closed(backend):
    """A context from before reservations names no holder to delete its framework state under.

    Its owner can still read and delete its tasks, but the state that may be
    left behind must never reach a new caller, so the context stays closed.
    """
    if backend.kind != "postgres":
        pytest.skip("blocked reservations exist only on a migrated database")
    agent_id = backend.store.agent_id
    task = Task(id=str(uuid.uuid4()), context_id="old", status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED))
    task.history.append(_message("m1"))
    async with db_manager.get_session() as session:
        await session.execute(
            text("INSERT INTO context_reservations (agent_id, context_id, kind) VALUES (:agent_id, 'old', 'blocked')"),
            {"agent_id": agent_id},
        )
        await session.execute(
            text("INSERT INTO context_bindings (agent_id, context_id, owner_scope) VALUES (:agent_id, 'old', 'alice')"),
            {"agent_id": agent_id},
        )
        await session.commit()
    await backend.save(task, "alice")
    assert await backend.history("alice", "old") == ["m1"]

    await backend.delete("alice", "old")

    assert await backend.task_count("old") == 0
    assert backend.deleted == []
    assert not await backend.admission.admit("old", ContextHolder.private("alice"), "alice")
    assert not await backend.admission.admit("old", ContextHolder.private("bob"), "bob")


async def test_a_message_admitted_before_the_deletion_creates_no_task_after_it(backend):
    """Admission came first, the deletion finished, then the task would be created: it is refused."""
    await backend.send("alice", "ctx", _message("m1"))
    assert await backend.admission.admit("ctx", ContextHolder.private("alice"), "alice")
    late = Task(id=str(uuid.uuid4()), context_id="ctx", status=TaskStatus(state=TaskState.TASK_STATE_WORKING))

    await backend.delete("alice", "ctx")

    with pytest.raises(ConcurrentTaskModificationError):
        await backend.save(late, "alice")
    assert await backend.task_count("ctx") == 0


async def test_a_pending_deletion_is_resumed_without_its_caller(backend):
    """What a restart leaves ``deleting`` is finished by the server itself."""
    await backend.send("alice", "ctx", _message("m1"), state=TaskState.TASK_STATE_WORKING)
    backend.unsettled = True
    with pytest.raises(ContextDeletionInProgress):
        await backend.delete("alice", "ctx")

    backend.unsettled = False
    await backend.service.resume_pending_deletions()

    assert await backend.task_count("ctx") == 0
    assert await backend.service._catalog.pending_deletions() == []

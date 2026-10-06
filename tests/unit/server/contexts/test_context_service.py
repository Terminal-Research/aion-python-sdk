"""The Context extension's contract over the in-memory task store and admission.

Real store, admission and catalog; only the two things that reach outside
storage - cancelling a task and deleting framework state - are fakes, so each
test can decide how they behave.
"""

from __future__ import annotations

from typing import Optional

import pytest
from a2a.server.context import ServerCallContext
from a2a.server.owner_resolver import resolve_user_scope
from a2a.server.routes.common import StarletteUser
from a2a.types import Artifact, Message, Part, Role, Task, TaskState, TaskStatus
from a2a.utils.errors import TaskNotCancelableError, UnsupportedOperationError
from starlette.authentication import SimpleUser

from aion.core.a2a import (
    A2AMetadataKey,
    ArtifactId,
    DeleteContextParams,
    GetContextParams,
    GetContextsParams,
    MessageType,
)
from aion.server.contexts import (
    ContextDeletionInProgress,
    ContextNotDeletable,
    ContextNotFound,
    ContextService,
    ContextStateDeleter,
)
from aion.server.tasks.admission import ACTIVE, DELETING, ContextHolder, InMemoryContextAdmission
from aion.server.tasks.contexts import InMemoryContextCatalog
from a2a.server.cluster.task_store import ConcurrentTaskModificationError
from aion.server.tasks.stores import InMemoryTaskStore

GATEWAY = ContextHolder.shared("identity", "environment")


def _caller(name: str) -> ServerCallContext:
    return ServerCallContext(user=StarletteUser(SimpleUser(name)))


def _message(message_id: str, text: str = "", role=Role.ROLE_USER, message_type: Optional[str] = None) -> Message:
    message = Message(message_id=message_id, role=role, parts=[Part(text=text or message_id)])
    if message_type is not None:
        message.metadata.update({A2AMetadataKey.MESSAGE_TYPE.value: message_type})
    return message


class _Agent:
    """Cancels by settling the task in the store, and records state deletions."""

    def __init__(self, store: InMemoryTaskStore) -> None:
        self.store = store
        self.cancelled: list[str] = []
        self.deleted: list[tuple[str, Optional[str], Optional[tuple[str, str]]]] = []
        self.cancel_behaviour = "settle"
        self.supported = True
        self.delete_error: Optional[BaseException] = None

    async def cancel(self, task_id: str):
        self.cancelled.append(task_id)
        if self.cancel_behaviour == "refuse":
            raise UnsupportedOperationError(message="the agent will not stop")
        if self.cancel_behaviour == "hang":
            raise TimeoutError("still settling")
        stored = self.store.tasks[task_id]
        if stored.task.status.state == TaskState.TASK_STATE_COMPLETED:
            raise TaskNotCancelableError()
        stored.task.status.state = TaskState.TASK_STATE_CANCELED
        return stored.task

    async def delete_state(self, context_id, *, owner_scope, gateway):
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted.append((context_id, owner_scope, gateway))


class _World:
    def __init__(self) -> None:
        self.store = InMemoryTaskStore(resolve_user_scope)
        self.admission = InMemoryContextAdmission()
        self.agent = _Agent(self.store)
        self.service = ContextService(
            catalog=InMemoryContextCatalog(self.store, self.admission),
            owner_resolver=resolve_user_scope,
            cancel_task=self.agent.cancel,
            state_deleter=ContextStateDeleter(supported=lambda: self.agent.supported, delete=self.agent.delete_state),
        )
        self._tasks = 0

    async def send(
        self,
        name: str,
        context_id: str,
        *messages: Message,
        holder: Optional[ContextHolder] = None,
        state=TaskState.TASK_STATE_COMPLETED,
        artifacts: tuple[Artifact, ...] = (),
        status_message: Optional[Message] = None,
    ) -> Task:
        assert await self.admission.admit(context_id, holder or ContextHolder.private(name), name)
        self._tasks += 1
        task = Task(
            id=f"task-{self._tasks}",
            context_id=context_id,
            status=TaskStatus(state=state),
            history=list(messages),
            artifacts=list(artifacts),
        )
        if status_message is not None:
            task.status.message.CopyFrom(status_message)
        await self.store.save(task, _caller(name))
        return task

    async def contexts(self, name: str, **params) -> list[str]:
        listing = await self.service.get_contexts(GetContextsParams(**params), _caller(name))
        return [summary.context_id for summary in listing.root]

    async def read(self, name: str, context_id: str, **params):
        return await self.service.get_context(GetContextParams(context_id=context_id, **params), _caller(name))

    async def delete(self, name: str, context_id: str):
        return await self.service.delete_context(DeleteContextParams(context_id=context_id), _caller(name))


@pytest.fixture
def world() -> _World:
    return _World()


class TestReads:
    async def test_contexts_are_listed_most_recent_first(self, world):
        await world.send("alice", "old", _message("m1"))
        await world.send("alice", "new", _message("m2"))
        await world.send("alice", "old", _message("m3"))

        assert await world.contexts("alice") == ["old", "new"]
        assert await world.contexts("alice", history_length=1, history_offset=1) == ["new"]

    async def test_history_is_chronological_across_tasks(self, world):
        await world.send("alice", "ctx", _message("m1"), _message("m2", role=Role.ROLE_AGENT))
        await world.send("alice", "ctx", _message("m3"), status_message=_message("m4", role=Role.ROLE_AGENT))

        view = await world.read("alice", "ctx")

        assert [message.message_id for message in view.history] == ["m1", "m2", "m3", "m4"]
        assert view.status.state == TaskState.TASK_STATE_COMPLETED
        assert view.title is None and view.summary is None

    async def test_pagination_selects_a_newest_relative_slice(self, world):
        await world.send("alice", "ctx", *(_message(f"m{i}") for i in range(1, 7)))

        view = await world.read("alice", "ctx", history_length=2, history_offset=1)

        assert [message.message_id for message in view.history] == ["m4", "m5"]

    async def test_internal_events_are_not_conversation(self, world):
        await world.send(
            "alice",
            "ctx",
            _message("m1"),
            _message("event", message_type=MessageType.EVENT.value),
            _message("m2", message_type=MessageType.MESSAGE.value),
        )

        view = await world.read("alice", "ctx")

        assert [message.message_id for message in view.history] == ["m1", "m2"]

    async def test_artifacts_are_qualified_by_task_newest_first_without_deltas(self, world):
        first = await world.send("alice", "ctx", artifacts=(Artifact(artifact_id="report"),))
        second = await world.send(
            "alice",
            "ctx",
            artifacts=(
                Artifact(artifact_id="report"),
                Artifact(artifact_id=ArtifactId.STREAM_DELTA.value),
                Artifact(artifact_id=ArtifactId.THINKING_DELTA.value),
            ),
        )

        view = await world.read("alice", "ctx")

        assert [(item.task_id, item.artifact.artifact_id) for item in view.artifacts] == [
            (second.id, "report"),
            (first.id, "report"),
        ]

    async def test_status_is_the_latest_tasks(self, world):
        await world.send("alice", "ctx", _message("m1"))
        await world.send("alice", "ctx", _message("m2"), state=TaskState.TASK_STATE_WORKING)

        assert (await world.read("alice", "ctx")).status.state == TaskState.TASK_STATE_WORKING

    async def test_a_context_without_tasks_is_visible_and_empty(self, world):
        await world.admission.admit("ctx", ContextHolder.private("alice"), "alice")

        view = await world.read("alice", "ctx")

        assert view.history == []
        assert view.status.state == TaskState.TASK_STATE_UNSPECIFIED
        assert await world.contexts("alice") == ["ctx"]

    async def test_knowing_a_context_id_grants_nothing(self, world):
        await world.send("alice", "ctx", _message("m1"))

        with pytest.raises(ContextNotFound):
            await world.read("bob", "ctx")
        with pytest.raises(ContextNotFound):
            await world.read("alice", "missing")
        assert await world.contexts("bob") == []

    async def test_a_shared_context_shows_every_participant(self, world):
        await world.send("alice", "ctx", _message("from-alice"), holder=GATEWAY)
        await world.send("bob", "ctx", _message("from-bob"), holder=GATEWAY)

        for name in ("alice", "bob"):
            view = await world.read(name, "ctx")
            assert [message.message_id for message in view.history] == ["from-alice", "from-bob"]

    async def test_a_caller_without_an_owner_sees_nothing(self, world):
        await world.send("alice", "ctx", _message("m1"))
        anonymous = ServerCallContext()

        assert (await world.service.get_contexts(GetContextsParams(), anonymous)).root == []
        with pytest.raises(ContextNotFound):
            await world.service.get_context(GetContextParams(context_id="ctx"), anonymous)


class TestDeletion:
    async def test_the_last_binding_deletes_everything(self, world):
        await world.send("alice", "ctx", _message("m1"))

        result = await world.delete("alice", "ctx")

        assert result.context_id == "ctx"
        assert world.store.tasks == {}
        assert world.agent.deleted == [("ctx", "alice", None)]
        assert await world.contexts("alice") == []
        with pytest.raises(ContextNotFound):
            await world.read("alice", "ctx")

    async def test_deletion_is_idempotent_and_discloses_nothing(self, world):
        await world.send("alice", "ctx", _message("m1"))

        await world.delete("bob", "ctx")
        await world.delete("bob", "missing")
        await world.delete("alice", "ctx")
        await world.delete("alice", "ctx")

        assert world.agent.deleted == [("ctx", "alice", None)]

    async def test_another_callers_delete_leaves_the_context_alone(self, world):
        await world.send("alice", "ctx", _message("m1"))

        await world.delete("bob", "ctx")

        assert [message.message_id for message in (await world.read("alice", "ctx")).history] == ["m1"]

    async def test_a_shared_context_survives_until_its_last_participant_leaves(self, world):
        await world.send("alice", "ctx", _message("from-alice"), holder=GATEWAY)
        await world.send("bob", "ctx", _message("from-bob"), holder=GATEWAY)

        await world.delete("alice", "ctx")

        assert len(world.store.tasks) == 2
        assert world.agent.deleted == []
        with pytest.raises(ContextNotFound):
            await world.read("alice", "ctx")
        assert len((await world.read("bob", "ctx")).history) == 2

        await world.delete("bob", "ctx")

        assert world.store.tasks == {}
        assert world.agent.deleted == [("ctx", None, ("identity", "environment"))]

    async def test_active_tasks_are_cancelled_first(self, world):
        await world.send("alice", "ctx", _message("m1"))
        working = await world.send("alice", "ctx", _message("m2"), state=TaskState.TASK_STATE_WORKING)

        await world.delete("alice", "ctx")

        assert world.agent.cancelled == [working.id]
        assert world.store.tasks == {}

    async def test_an_unsettled_cancellation_keeps_the_deletion_durable(self, world):
        working = await world.send("alice", "ctx", _message("m1"), state=TaskState.TASK_STATE_WORKING)
        world.agent.cancel_behaviour = "hang"

        with pytest.raises(ContextDeletionInProgress) as raised:
            await world.delete("alice", "ctx")

        operation_id = raised.value.deletion_operation_id
        assert operation_id is not None
        assert raised.value.data()["retryable"] is True
        entry = world.admission.entries["ctx"]
        assert entry.state == DELETING
        # Fenced: no new work, from anyone.
        assert not await world.admission.admit("ctx", ContextHolder.private("alice"), "alice")
        # Another caller learns nothing and changes nothing.
        await world.delete("bob", "ctx")
        assert entry.state == DELETING

        world.agent.cancel_behaviour = "settle"
        world.store.tasks[working.id].task.status.state = TaskState.TASK_STATE_CANCELED
        await world.delete("alice", "ctx")

        assert world.store.tasks == {}
        assert entry.deletion_operation_id == operation_id

    async def test_a_refused_cancellation_restores_the_context_without_the_binding(self, world):
        await world.send("alice", "ctx", _message("m1"), state=TaskState.TASK_STATE_WORKING)
        world.agent.cancel_behaviour = "refuse"

        with pytest.raises(ContextNotDeletable) as raised:
            await world.delete("alice", "ctx")

        assert raised.value.data()["retryable"] is False
        entry = world.admission.entries["ctx"]
        assert entry.state == ACTIVE
        assert entry.bindings == {}
        assert len(world.store.tasks) == 1
        assert world.agent.deleted == []

    async def test_state_that_cannot_be_deleted_refuses_the_last_binding_before_any_cancellation(self, world):
        await world.send("alice", "ctx", _message("m1"), state=TaskState.TASK_STATE_WORKING)
        world.agent.supported = False

        with pytest.raises(ContextNotDeletable):
            await world.delete("alice", "ctx")

        entry = world.admission.entries["ctx"]
        assert entry.state == ACTIVE
        assert entry.bindings == {}
        assert world.agent.cancelled == []
        assert len(world.store.tasks) == 1

    async def test_state_that_cannot_be_deleted_still_lets_a_participant_leave(self, world):
        await world.send("alice", "ctx", _message("from-alice"), holder=GATEWAY)
        await world.send("bob", "ctx", _message("from-bob"), holder=GATEWAY)
        world.agent.supported = False

        await world.delete("alice", "ctx")
        await world.delete("alice", "never-used")

        with pytest.raises(ContextNotFound):
            await world.read("alice", "ctx")
        assert len((await world.read("bob", "ctx")).history) == 2

    async def test_a_deletion_superseded_by_another_attempt_is_not_reported_finished(self, world):
        await world.send("alice", "ctx", _message("m1"))
        ticket = await world.service._catalog.begin_deletion("ctx", "alice")
        await world.service._catalog.abandon_deletion("ctx", ticket.operation_id)

        with pytest.raises(ContextDeletionInProgress):
            await world.service._drive("ctx", ticket)

        assert len(world.store.tasks) == 1

    async def test_a_message_admitted_before_the_deletion_creates_no_task_after_it(self, world):
        await world.send("alice", "ctx", _message("m1"))
        late = Task(id="late", context_id="ctx", status=TaskStatus(state=TaskState.TASK_STATE_WORKING))

        await world.delete("alice", "ctx")

        with pytest.raises(ConcurrentTaskModificationError):
            await world.store.save(late, _caller("alice"))
        assert world.store.tasks == {}

    async def test_pending_deletions_are_resumed_without_their_caller(self, world):
        working = await world.send("alice", "ctx", _message("m1"), state=TaskState.TASK_STATE_WORKING)
        world.agent.cancel_behaviour = "hang"
        with pytest.raises(ContextDeletionInProgress):
            await world.delete("alice", "ctx")

        world.agent.cancel_behaviour = "settle"
        await world.service.resume_pending_deletions()

        assert world.agent.cancelled == [working.id, working.id]
        assert world.store.tasks == {}
        assert world.admission.entries["ctx"].state != DELETING

    async def test_a_failed_state_deletion_is_retried(self, world):
        await world.send("alice", "ctx", _message("m1"))
        world.agent.delete_error = ConnectionError("database away")

        with pytest.raises(ContextDeletionInProgress):
            await world.delete("alice", "ctx")
        assert len(world.store.tasks) == 1

        world.agent.delete_error = None
        await world.delete("alice", "ctx")

        assert world.store.tasks == {}

    async def test_a_late_write_cannot_restore_retired_history(self, world):
        task = await world.send("alice", "ctx", _message("m1"))
        await world.delete("alice", "ctx")

        with pytest.raises(ConcurrentTaskModificationError):
            await world.store.save(task, _caller("alice"))

        assert world.store.tasks == {}

    async def test_a_deleted_context_id_starts_a_new_conversation(self, world):
        await world.send("alice", "ctx", _message("m1"))
        await world.delete("alice", "ctx")

        await world.send("bob", "ctx", _message("fresh"))

        assert [message.message_id for message in (await world.read("bob", "ctx")).history] == ["fresh"]
        with pytest.raises(ContextNotFound):
            await world.read("alice", "ctx")

    async def test_a_caller_without_an_owner_deletes_nothing(self, world):
        await world.send("alice", "ctx", _message("m1"))

        await world.service.delete_context(DeleteContextParams(context_id="ctx"), ServerCallContext())

        assert len(world.store.tasks) == 1

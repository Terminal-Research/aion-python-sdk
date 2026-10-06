"""Resubscribing in AionRequestHandler: a2a-sdk's subscription, closed by a Task.

A task that already has an outcome is refused with ``UnsupportedOperationError``,
as a2a-sdk refuses it, and the refusal is not turned into a replay of the
stored task. A task running on another instance is served by a2a-sdk as a
snapshot followed by the task's journal; the handler closes that stream with
the stored task, withholding the journal's own closing status update.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from unittest.mock import Mock

import pytest
from a2a.auth.user import User
from a2a.server.cluster import StoredTask, TaskEventStream, TaskVersion, VersionedEvent, VersionedTaskStore
from a2a.server.context import ServerCallContext
from a2a.types import (
    Message,
    Part,
    Role,
    SubscribeToTaskRequest,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils.errors import UnsupportedOperationError

from aion.server.core.app.handlers.request_handler import AionRequestHandler
from aion.server.tasks.stores import InMemoryTaskStore

TASK_ID = str(uuid.uuid4())


class _Caller(User):
    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return "alice"


CONTEXT = ServerCallContext(user=_Caller())


@pytest.fixture
def anyio_backend():
    """Run async tests on asyncio only."""
    return "asyncio"


def _task(state: TaskState, text: str | None = None) -> Task:
    status = TaskStatus(state=state)
    if text is not None:
        status.message.CopyFrom(Message(message_id=f"m-{text}", role=Role.ROLE_AGENT, parts=[Part(text=text)]))
    return Task(id=TASK_ID, context_id="ctx", status=status)


def _card() -> Mock:
    card = Mock()
    card.capabilities.streaming = True
    return card


class _Versioned(VersionedTaskStore):
    """A versioned store over an in-memory one, at a fixed version."""

    def __init__(self, store: InMemoryTaskStore, version: int) -> None:
        self.as_task_store = store
        self.version = version

    async def save(self, task, *, event, prev, prev_version, context):  # pragma: no cover - unused
        await self.as_task_store.save(task, context)
        return TaskVersion(self.version)

    async def get(self, task_id, context):
        task = await self.as_task_store.get(task_id, context)
        return None if task is None else StoredTask(task, TaskVersion(self.version))

    async def list(self, params, context):  # pragma: no cover - unused
        return await self.as_task_store.list(params, context)

    async def delete(self, task_id, context):  # pragma: no cover - unused
        await self.as_task_store.delete(task_id, context)


class _Journal(TaskEventStream):
    """A stream that yields a fixed tail and stores the final state as it goes."""

    def __init__(self, store: InMemoryTaskStore, events: list[VersionedEvent], final: Task) -> None:
        self._store = store
        self._events = events
        self._final = final
        self.subscribed_after: list[TaskVersion] = []

    async def publish(self, task_id, event):  # pragma: no cover - unused
        pass

    async def subscribe(self, task_id, *, after) -> AsyncGenerator[VersionedEvent, None]:
        self.subscribed_after.append(after)
        for event in self._events:
            yield event
        await self._store.save(self._final, CONTEXT)

    async def destroy(self, task_id):  # pragma: no cover - unused
        pass


async def _collect(handler: AionRequestHandler) -> list:
    return [
        event
        async for event in handler.on_subscribe_to_task(SubscribeToTaskRequest(id=TASK_ID), CONTEXT)
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "state",
    [TaskState.TASK_STATE_COMPLETED, TaskState.TASK_STATE_FAILED, TaskState.TASK_STATE_CANCELED],
)
async def test_a_task_with_an_outcome_is_refused(state: TaskState) -> None:
    """Nothing is left to stream, and the stored task is not replayed instead."""
    store = InMemoryTaskStore()
    await store.save(_task(state), CONTEXT)
    handler = AionRequestHandler(agent_executor=Mock(), task_store=store, agent_card=_card())

    with pytest.raises(UnsupportedOperationError, match=TaskState.Name(state)):
        await _collect(handler)


@pytest.mark.anyio
async def test_a_task_running_elsewhere_is_followed_from_the_journal_and_closed_by_a_task() -> None:
    """Snapshot, journal tail, and the stored task in place of the closing status."""
    store = InMemoryTaskStore()
    await store.save(_task(TaskState.TASK_STATE_WORKING, "thinking"), CONTEXT)
    final = _task(TaskState.TASK_STATE_COMPLETED, "done")
    progress = TaskStatusUpdateEvent(task_id=TASK_ID, context_id="ctx", status=_task(TaskState.TASK_STATE_WORKING, "more").status)
    closing = TaskStatusUpdateEvent(task_id=TASK_ID, context_id="ctx", status=final.status)
    journal = _Journal(
        store,
        [VersionedEvent(progress, TaskVersion(4)), VersionedEvent(closing, TaskVersion(5))],
        final,
    )
    handler = AionRequestHandler(
        agent_executor=Mock(),
        task_store=_Versioned(store, version=3),
        agent_card=_card(),
        event_stream=journal,
    )

    events = await _collect(handler)

    assert journal.subscribed_after == [TaskVersion(3)]
    assert isinstance(events[0], Task) and events[0].status.state == TaskState.TASK_STATE_WORKING
    assert events[1] == progress
    assert isinstance(events[-1], Task) and events[-1].status.state == TaskState.TASK_STATE_COMPLETED
    assert closing not in events, "the journal's closing status reached the client as well as the Task"

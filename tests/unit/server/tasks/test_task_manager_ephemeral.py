"""Integration test for the ephemeral-status-event path of AionTaskManager.

A status update flagged ephemeral (via `mark_status_event_ephemeral`) must be
streamed to the client — `process` still returns it — but must never land in
`task.history`. This is the persistence half of the milestone-only history
policy: live progress (running commands, intermediate agent messages) streams
without accumulating in the durable task record.
"""

import uuid

import pytest
from a2a.server.context import ServerCallContext
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    Message,
    Part,
    Role,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)

from aion.server.a2a.utils import mark_status_event_ephemeral
from aion.server.agent.execution.scope import (
    clear_execution_scope,
    init_execution_scope,
)

from aion.server.tasks.task_manager import AionTaskManager

TASK_ID = "task-1"
CONTEXT_ID = "ctx-1"


@pytest.fixture(autouse=True)
def _execution_scope():
    # AionTaskManager._track_task_status writes task status into the per-request
    # execution scope for every persisted status update, so one must be set.
    init_execution_scope()
    yield
    clear_execution_scope()


def _status_event(text: str, *, ephemeral: bool = False) -> TaskStatusUpdateEvent:
    message = Message(
        context_id=CONTEXT_ID,
        task_id=TASK_ID,
        message_id=str(uuid.uuid4()),
        role=Role.ROLE_AGENT,
        parts=[Part(text=text)],
    )
    event = TaskStatusUpdateEvent(
        task_id=TASK_ID,
        context_id=CONTEXT_ID,
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING, message=message),
    )
    if ephemeral:
        mark_status_event_ephemeral(event)
    return event


def _manager(store: InMemoryTaskStore, context: ServerCallContext) -> AionTaskManager:
    return AionTaskManager(
        task_store=store,
        context=context,
        task_id=TASK_ID,
        context_id=CONTEXT_ID,
        initial_message=None,
    )


async def test_ephemeral_status_event_is_streamed_but_not_persisted():
    store = InMemoryTaskStore()
    context = ServerCallContext()
    manager = _manager(store, context)

    durable_a = _status_event("milestone A")
    ephemeral = _status_event("running $ pytest -q", ephemeral=True)
    durable_b = _status_event("milestone B")

    await manager.process(durable_a)
    returned = await manager.process(ephemeral)
    await manager.process(durable_b)

    # Streamed: process returns the ephemeral event untouched, so the SSE
    # consumer still delivers the frame to the client.
    assert returned is ephemeral

    task = await store.get(TASK_ID, context)
    history_texts = [m.parts[0].text for m in task.history]

    # Durable A was folded into history when B arrived; the ephemeral event
    # never became status.message, so it was never folded.
    assert "milestone A" in history_texts
    assert "running $ pytest -q" not in history_texts
    # The current status message is the last durable milestone.
    assert task.status.message.parts[0].text == "milestone B"


async def test_ephemeral_flag_absent_by_default_so_durable_events_persist():
    store = InMemoryTaskStore()
    context = ServerCallContext()
    manager = _manager(store, context)

    await manager.process(_status_event("milestone A"))
    await manager.process(_status_event("milestone B"))
    await manager.process(_status_event("milestone C"))

    task = await store.get(TASK_ID, context)
    history_texts = [m.parts[0].text for m in task.history]
    assert history_texts == ["milestone A", "milestone B"]
    assert task.status.message.parts[0].text == "milestone C"

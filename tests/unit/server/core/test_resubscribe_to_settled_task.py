"""Resubscribing to a task that already has an outcome.

The registry answers such a task with ``None``: there is no execution to join,
and the SDK's ``ActiveTask.start`` refuses a terminal task outright. What is
left to check is that the handler turns that ``None`` into the reply the client
asked for - the stored task, once - rather than into an empty stream or an
error.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, Mock

import pytest
from a2a.server.context import ServerCallContext
from a2a.types import SubscribeToTaskRequest, Task, TaskState, TaskStatus

from aion.server.core.app.handlers.request_handler import AionRequestHandler

TASK_ID = str(uuid.uuid4())


@pytest.fixture
def anyio_backend():
    """Run async tests on asyncio only."""
    return "asyncio"


def _settled_task(state: TaskState) -> Task:
    """Build the durable snapshot of a finished task."""
    return Task(id=TASK_ID, context_id="ctx", status=TaskStatus(state=state))


def _handler(task: Task) -> AionRequestHandler:
    """Build a handler whose store holds one task and whose registry attaches to none."""
    agent_card = Mock()
    agent_card.capabilities.streaming = True
    handler = AionRequestHandler(
        agent_executor=Mock(),
        task_store=AsyncMock(),
        agent_card=agent_card,
    )
    handler.task_store.get = AsyncMock(return_value=task)
    handler._active_task_registry.get_for_attach = AsyncMock(return_value=None)
    return handler


@pytest.mark.anyio
@pytest.mark.parametrize(
    "state",
    [
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
    ],
)
async def test_a_settled_task_is_replayed_once_and_the_stream_closes(
    state: TaskState,
) -> None:
    """The outcome exists to be read; this is the request that reads it."""
    task = _settled_task(state)
    handler = _handler(task)

    events = [
        event
        async for event in handler.on_subscribe_to_task(
            SubscribeToTaskRequest(id=TASK_ID),
            Mock(spec=ServerCallContext),
        )
    ]

    assert len(events) == 1
    assert isinstance(events[0], Task)
    assert events[0].id == TASK_ID
    assert events[0].status.state == state

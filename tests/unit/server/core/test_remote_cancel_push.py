"""A remote cancel - ``CANCELED`` written over a task this server does not run - is pushed by its writer."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, Mock

import pytest
from a2a.auth.user import User
from a2a.server.context import ServerCallContext
from a2a.types import CancelTaskRequest, Task, TaskState, TaskStatus

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
    return "asyncio"


async def _handler(push_sender) -> AionRequestHandler:
    store = InMemoryTaskStore()
    await store.save(
        Task(id=TASK_ID, context_id="ctx", status=TaskStatus(state=TaskState.TASK_STATE_WORKING)), CONTEXT
    )
    card = Mock()
    card.capabilities.streaming = True
    return AionRequestHandler(agent_executor=Mock(), task_store=store, agent_card=card, push_sender=push_sender)


@pytest.mark.anyio
async def test_the_canceled_task_is_pushed_once() -> None:
    push_sender = Mock()
    push_sender.send_notification = AsyncMock()
    handler = await _handler(push_sender)

    cancelled = await handler.on_cancel_task(CancelTaskRequest(id=TASK_ID), CONTEXT)

    assert cancelled.status.state == TaskState.TASK_STATE_CANCELED
    push_sender.send_notification.assert_awaited_once()
    task_id, pushed = push_sender.send_notification.await_args.args
    assert (task_id, pushed.status.state) == (TASK_ID, TaskState.TASK_STATE_CANCELED)


@pytest.mark.anyio
async def test_a_failed_push_does_not_undo_the_cancel() -> None:
    push_sender = Mock()
    push_sender.send_notification = AsyncMock(side_effect=RuntimeError("receiver down"))
    handler = await _handler(push_sender)

    cancelled = await handler.on_cancel_task(CancelTaskRequest(id=TASK_ID), CONTEXT)

    assert cancelled.status.state == TaskState.TASK_STATE_CANCELED

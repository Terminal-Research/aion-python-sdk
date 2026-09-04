"""The unary `message/send` path answers with a Task, like the streaming one.

The base handler may answer with a Message when the agent never created a task.
Aion's outbound contract is uniform across transports and methods: the caller
always receives a Task carrying a non-active state.
"""

import pytest
from a2a.server.request_handlers import DefaultRequestHandlerV2
from a2a.utils.errors import InternalError
from a2a.types import (
    Message,
    Part,
    Role,
    SendMessageRequest,
    Task,
    TaskState,
    TaskStatus,
)
from unittest.mock import AsyncMock, Mock, patch

from aion.server.core.app.handlers.request_handler import AionRequestHandler

TASK_ID = "task-1"
CONTEXT_ID = "ctx-1"
ANSWER = "SUMMER"


@pytest.fixture
def anyio_backend():
    """Run async tests on asyncio only."""
    return "asyncio"


def _agent_message(task_id: str = TASK_ID) -> Message:
    message = Message(
        message_id="msg-agent",
        context_id=CONTEXT_ID,
        role=Role.ROLE_AGENT,
        parts=[Part(text=ANSWER)],
    )
    if task_id:
        message.task_id = task_id
    return message


def _stored_task() -> Task:
    task = Task(
        id=TASK_ID,
        context_id=CONTEXT_ID,
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED, message=_agent_message()),
    )
    task.history.append(
        Message(
            message_id="msg-user",
            context_id=CONTEXT_ID,
            task_id=TASK_ID,
            role=Role.ROLE_USER,
            parts=[Part(text="upper summer")],
        )
    )
    return task


def _request(history_length: int | None = None) -> SendMessageRequest:
    request = SendMessageRequest()
    request.message.CopyFrom(
        Message(
            message_id="msg-user",
            context_id=CONTEXT_ID,
            role=Role.ROLE_USER,
            parts=[Part(text="upper summer")],
        )
    )
    if history_length is not None:
        request.configuration.history_length = history_length
    return request


def _handler(stored: Task | None) -> AionRequestHandler:
    task_store = Mock()
    task_store.get = AsyncMock(return_value=stored)
    task_store.save = AsyncMock()
    return AionRequestHandler(
        agent_executor=Mock(),
        task_store=task_store,
        agent_card=Mock(),
    )


async def _send(handler: AionRequestHandler, base_result, params=None):
    async def fake_send(self, params, context):
        return base_result

    with patch.object(DefaultRequestHandlerV2, 'on_message_send', fake_send):
        return await handler.on_message_send(params or _request(), Mock())


class TestUnaryTaskContract:
    """`message/send` never hands a bare Message back to the caller."""

    @pytest.mark.anyio
    async def test_task_result_is_returned_untouched(self):
        """The common case is already a Task and must not be re-read or re-cut."""
        stored = _stored_task()
        handler = _handler(stored)

        result = await _send(handler, stored)

        assert result is stored
        handler.task_store.get.assert_not_awaited()

    @pytest.mark.anyio
    async def test_message_result_is_resolved_from_the_store(self):
        """A message-only reply is answered with the task it belongs to."""
        stored = _stored_task()
        handler = _handler(stored)

        result = await _send(handler, _agent_message())

        assert isinstance(result, Task)
        assert result.id == TASK_ID
        assert result.status.state == TaskState.TASK_STATE_COMPLETED
        assert [part.text for part in result.status.message.parts] == [ANSWER]

    @pytest.mark.anyio
    async def test_resolved_task_honours_history_length(self):
        """The truncation the base handler applies to a Task applies here too."""
        handler = _handler(_stored_task())

        result = await _send(handler, _agent_message(), _request(history_length=0))

        assert list(result.history) == []
        assert [part.text for part in result.status.message.parts] == [ANSWER]

    @pytest.mark.anyio
    async def test_message_without_a_stored_task_fails_loudly(self):
        """A task id that resolves to nothing is a broken executor, not a result."""
        handler = _handler(None)

        with pytest.raises(InternalError):
            await _send(handler, _agent_message())

    @pytest.mark.anyio
    async def test_message_without_a_task_id_fails_loudly(self):
        """Aion's executor always announces a Task, so message mode is a defect."""
        handler = _handler(_stored_task())

        with pytest.raises(InternalError):
            await _send(handler, _agent_message(task_id=""))

        handler.task_store.get.assert_not_awaited()

    @pytest.mark.anyio
    async def test_task_id_falls_back_to_the_request(self):
        """The message may omit the id the request already established."""
        handler = _handler(_stored_task())
        params = _request()
        params.message.task_id = TASK_ID

        result = await _send(handler, _agent_message(task_id=""), params)

        assert result.id == TASK_ID
        handler.task_store.get.assert_awaited_once()

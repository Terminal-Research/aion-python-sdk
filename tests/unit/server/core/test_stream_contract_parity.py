"""Parity between the v1.0 and v0.3 outbound stream contracts.

Both wire versions are advertised on the agent card (see AgentCardBuilder), so
the same agent event stream must reach a v0.3 client exactly as it reaches a
v1.0 client: same events, same order, differing only by type conversion.

In v0.3 the `final` flag is advisory — the reference client never reads it and
terminates on SSE close (a2a-python v0.3, transports/jsonrpc.py), while
`SendStreamingMessageSuccessResponse.result` explicitly admits a Task. So the
terminal Task needs no trailing marker and none is synthesized.
"""

import pytest
from a2a.compat.v0_3 import types as types_v03
from a2a.compat.v0_3.request_handler import RequestHandler03
from a2a.server.request_handlers import DefaultRequestHandlerV2
from a2a.types import (
    Message,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
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


def _agent_message() -> Message:
    return Message(
        message_id="msg-agent",
        context_id=CONTEXT_ID,
        task_id=TASK_ID,
        role=Role.ROLE_AGENT,
        parts=[Part(text=ANSWER)],
    )


def _raw_agent_stream() -> list:
    """The events an executor produces for a single completed turn."""
    return [
        Task(id=TASK_ID, context_id=CONTEXT_ID, status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED)),
        TaskStatusUpdateEvent(
            task_id=TASK_ID,
            context_id=CONTEXT_ID,
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        ),
        TaskStatusUpdateEvent(
            task_id=TASK_ID,
            context_id=CONTEXT_ID,
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING, message=_agent_message()),
        ),
        TaskStatusUpdateEvent(
            task_id=TASK_ID,
            context_id=CONTEXT_ID,
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        ),
    ]


def _stored_task() -> Task:
    """What the task manager has persisted by the time the stream ends.

    The closing message is carried onto the terminal status rather than demoted
    to history — see AionTaskManager._carry_pending_message.
    """
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


@pytest.fixture
def handler():
    """Aion request handler whose SDK stream is replaced by a canned sequence."""
    task_store = Mock()
    task_store.get = AsyncMock(return_value=_stored_task())
    handler = AionRequestHandler(
        agent_executor=Mock(),
        task_store=task_store,
        agent_card=Mock(),
    )

    async def fake_stream(self, params, context):
        for event in _raw_agent_stream():
            yield event

    with patch.object(DefaultRequestHandlerV2, 'on_message_send_stream', fake_stream):
        yield handler


def _core_request():
    from a2a.types import SendMessageRequest
    request = SendMessageRequest()
    request.message.CopyFrom(
        Message(
            message_id="msg-user",
            context_id=CONTEXT_ID,
            role=Role.ROLE_USER,
            parts=[Part(text="upper summer")],
        )
    )
    return request


def _compat_request() -> types_v03.SendMessageRequest:
    return types_v03.SendMessageRequest(
        id="req-1",
        params=types_v03.MessageSendParams(
            message=types_v03.Message(
                message_id="msg-user",
                context_id=CONTEXT_ID,
                role=types_v03.Role.user,
                parts=[types_v03.Part(root=types_v03.TextPart(text="upper summer"))],
            )
        ),
    )


async def _v10_events(handler: AionRequestHandler) -> list:
    return [
        event
        async for event in handler.on_message_send_stream(_core_request(), Mock())
    ]


async def _v03_results(handler: AionRequestHandler) -> list:
    compat = RequestHandler03(request_handler=handler)
    return [
        response.result
        async for response in compat.on_message_send_stream(_compat_request(), Mock())
    ]


class TestStreamContractParity:
    """The two wire versions carry the same stream, down to the event count."""

    @pytest.mark.anyio
    async def test_v10_stream_ends_with_the_task(self, handler):
        """No terminal status update reaches a v1.0 client."""
        events = await _v10_events(handler)

        assert [type(event) for event in events] == [
            Task,
            TaskStatusUpdateEvent,
            TaskStatusUpdateEvent,
            Task,
        ]
        assert events[-1].status.state == TaskState.TASK_STATE_COMPLETED
        assert not any(
            isinstance(event, TaskStatusUpdateEvent)
            and event.status.state == TaskState.TASK_STATE_COMPLETED
            for event in events
        )

    @pytest.mark.anyio
    async def test_v03_stream_ends_with_the_task_too(self, handler):
        """v0.3 sees the same events in the same order, with nothing appended."""
        results = await _v03_results(handler)

        assert [type(result) for result in results] == [
            types_v03.Task,
            types_v03.TaskStatusUpdateEvent,
            types_v03.TaskStatusUpdateEvent,
            types_v03.Task,
        ]
        assert results[-1].status.state == types_v03.TaskState.completed

    @pytest.mark.anyio
    async def test_no_v03_event_claims_to_be_final(self, handler):
        """The stream ends by closing, not by a marker the reference client ignores."""
        results = await _v03_results(handler)

        assert not any(
            isinstance(result, types_v03.TaskStatusUpdateEvent) and result.final
            for result in results
        )

    @pytest.mark.anyio
    async def test_the_answer_is_streamed_live_and_kept_in_the_final_status(self, handler):
        """The client sees the answer as it is produced and again as the closing status."""
        events = await _v10_events(handler)

        streamed_text = [
            part.text
            for event in events
            if isinstance(event, TaskStatusUpdateEvent) and event.status.HasField('message')
            for part in event.status.message.parts
        ]
        assert streamed_text == [ANSWER]

        final_task = events[-1]
        assert [part.text for part in final_task.status.message.parts] == [ANSWER]
        assert [m.message_id for m in final_task.history] == ["msg-user"]

    @pytest.mark.anyio
    async def test_both_versions_carry_the_answer_in_the_task(self, handler):
        """The final text sits in status.message on both wires, surviving truncation."""
        v10_task = (await _v10_events(handler))[-1]
        v03_task = (await _v03_results(handler))[-1]

        assert [part.text for part in v10_task.status.message.parts] == [ANSWER]
        assert [part.root.text for part in v03_task.status.message.parts] == [ANSWER]

    @pytest.mark.anyio
    async def test_v03_task_matches_the_v10_task(self, handler):
        """The snapshot content does not diverge between the two wires."""
        v10_task = (await _v10_events(handler))[-1]
        v03_task = (await _v03_results(handler))[-1]

        assert v03_task.id == v10_task.id
        assert v03_task.context_id == v10_task.context_id
        assert [m.message_id for m in v03_task.history] == [
            m.message_id for m in v10_task.history
        ]
        assert v03_task.status.message.message_id == v10_task.status.message.message_id

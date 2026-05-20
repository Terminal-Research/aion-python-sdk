from unittest.mock import AsyncMock, Mock, patch

import pytest
from a2a.types import Message, Part, Role, TaskState, TaskStatusUpdateEvent
from aion.server.agent.adapters import ExecutionSnapshot, ExecutionStatus
from aion.server.agent.exceptions import ExecutionError, StateRetrievalError

from aion.langgraph.server.execution.langgraph_executor import LangGraphExecutor
from aion.langgraph.server.execution.stream_executor import StreamResult

from ..helpers import make_execution_config, make_mock_request_context


async def make_astream(*events):
    for event in events:
        yield event


def make_context(message=None):
    ctx = make_mock_request_context(task_id="task-1", context_id="ctx-1", message=message)
    ctx.current_task = None
    ctx.metadata = {}
    ctx.get_user_input.return_value = "resume text"
    return ctx


def make_config(context_id="ctx-1"):
    return make_execution_config(context_id=context_id)


def make_snapshot(interrupted=False, metadata=None):
    return ExecutionSnapshot(
        state={},
        status=ExecutionStatus.INTERRUPTED if interrupted else ExecutionStatus.COMPLETE,
        metadata=metadata or {},
    )


class TestStream:
    async def test_stream_yields_stream_events_and_completion(self):
        stream_event = TaskStatusUpdateEvent(
            task_id="task-1",
            context_id="ctx-1",
            status={"state": TaskState.TASK_STATE_WORKING},
        )
        graph = Mock()
        graph.astream.return_value = make_astream(("custom", object()))
        graph.aget_state = AsyncMock(return_value=Mock())
        executor = LangGraphExecutor(compiled_graph=graph, config=Mock())

        with patch(
            "aion.langgraph.server.execution.event_converter.LangGraphA2AConverter.convert",
            return_value=[stream_event],
        ), patch.object(
            executor._state_adapter,
            "get_state_from_snapshot",
            return_value=make_snapshot(interrupted=False),
        ), patch.object(
            executor._result_handler,
            "handle",
            return_value=[],
        ):
            events = [event async for event in executor.stream(make_context(), make_config())]

        assert events[0] is stream_event
        assert events[-1].status.state == TaskState.TASK_STATE_COMPLETED

    async def test_stream_emits_result_handler_events_before_terminal_status(self):
        result_message = Message(
            task_id="task-1",
            context_id="ctx-1",
            message_id="msg-1",
            role=Role.ROLE_AGENT,
            parts=[Part(text="final")],
        )
        graph = Mock()
        graph.astream.return_value = make_astream()
        graph.aget_state = AsyncMock(return_value=Mock())
        executor = LangGraphExecutor(compiled_graph=graph, config=Mock())

        with patch.object(
            executor._state_adapter,
            "get_state_from_snapshot",
            return_value=make_snapshot(interrupted=False),
        ), patch.object(
            executor._result_handler,
            "handle",
            return_value=[result_message],
        ):
            events = [event async for event in executor.stream(make_context(), make_config())]

        assert events[0] is result_message
        assert events[1].status.state == TaskState.TASK_STATE_COMPLETED

    async def test_stream_wraps_failures_as_execution_error_after_error_event(self):
        graph = Mock()
        graph.astream.side_effect = RuntimeError("boom")
        executor = LangGraphExecutor(compiled_graph=graph, config=Mock())

        with pytest.raises(ExecutionError):
            [event async for event in executor.stream(make_context(), make_config())]


class TestResume:
    async def test_resume_requires_context_id(self):
        executor = LangGraphExecutor(compiled_graph=Mock(), config=Mock())

        with pytest.raises(ValueError):
            [event async for event in executor.resume(make_context(), make_config(context_id=None))]

    async def test_resume_interrupted_state_uses_resume_command(self):
        graph = Mock()
        graph.astream.return_value = make_astream()
        graph.aget_state = AsyncMock(return_value=Mock())
        executor = LangGraphExecutor(compiled_graph=graph, config=Mock())
        interrupted = make_snapshot(
            interrupted=True,
            metadata={"interrupt_data": [{"id": "i-1", "value": "Need input"}]},
        )

        with patch.object(
            executor._state_adapter,
            "get_state_from_snapshot",
            return_value=interrupted,
        ), patch.object(
            executor._result_handler,
            "handle",
            return_value=[],
        ):
            events = [event async for event in executor.resume(make_context(), make_config())]

        astream_inputs = graph.astream.call_args.args[0]
        assert astream_inputs.resume == {"messages": []}
        assert events[-1].status.state == TaskState.TASK_STATE_INPUT_REQUIRED

    async def test_resume_non_interrupted_state_with_input_delegates_to_stream(self):
        executor = LangGraphExecutor(compiled_graph=Mock(), config=Mock())

        with patch.object(executor, "get_state", new=AsyncMock(return_value=make_snapshot())), \
             patch.object(executor, "stream", return_value=make_astream("event")) as stream:
            events = [event async for event in executor.resume(make_context(), make_config())]

        assert events == ["event"]
        stream.assert_called_once()

    async def test_resume_non_interrupted_state_without_input_raises_execution_error(self):
        executor = LangGraphExecutor(compiled_graph=Mock(), config=Mock())
        context = make_context()
        context.get_user_input.return_value = ""

        with patch.object(executor, "get_state", new=AsyncMock(return_value=make_snapshot())):
            with pytest.raises(ExecutionError):
                [event async for event in executor.resume(context, make_config())]


class TestGetStateAndFinalize:
    async def test_get_state_converts_graph_snapshot(self):
        graph_snapshot = Mock()
        graph = Mock()
        graph.aget_state = AsyncMock(return_value=graph_snapshot)
        executor = LangGraphExecutor(compiled_graph=graph, config=Mock())
        expected = make_snapshot()

        with patch.object(executor._state_adapter, "get_state_from_snapshot", return_value=expected):
            result = await executor.get_state(make_config())

        assert result is expected

    async def test_get_state_wraps_graph_errors(self):
        graph = Mock()
        graph.aget_state = AsyncMock(side_effect=RuntimeError("state failed"))
        executor = LangGraphExecutor(compiled_graph=graph, config=Mock())

        with pytest.raises(StateRetrievalError):
            await executor.get_state(make_config())

    async def test_finalize_emits_interrupt_for_input_required_snapshot(self):
        executor = LangGraphExecutor(compiled_graph=Mock(), config=Mock())
        interrupted = make_snapshot(
            interrupted=True,
            metadata={"interrupt_data": [{"id": "i-1", "value": "Need input"}]},
        )

        with patch.object(executor, "get_state", new=AsyncMock(return_value=interrupted)), \
             patch.object(executor._result_handler, "handle", return_value=[]):
            events = [
                event
                async for event in executor._finalize(
                    StreamResult(delta_text=""),
                    make_config(),
                    make_context(),
                    converter=Mock(wraps=__import__(
                        "aion.langgraph.server.execution.event_converter",
                        fromlist=["LangGraphA2AConverter"],
                    ).LangGraphA2AConverter(task_id="task-1", context_id="ctx-1")),
                )
            ]

        assert events[-1].status.state == TaskState.TASK_STATE_INPUT_REQUIRED

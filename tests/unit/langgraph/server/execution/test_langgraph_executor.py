from unittest.mock import AsyncMock, Mock, patch

import pytest
from a2a.types import Message, Part, Role, TaskState, TaskStatusUpdateEvent
from aion.core.a2a import A2AOutbox, ArtifactId
from langchain_core.messages import AIMessageChunk
from aion.server.agent.adapters import LegacyStateError
from aion.server.agent.exceptions import ExecutionError, StateRetrievalError

from aion.langgraph.server.execution.event_converter import LangGraphA2AConverter
from aion.langgraph.server.execution.langgraph_executor import LangGraphExecutor
from aion.langgraph.server.execution.stream_executor import StreamResult

from ..helpers import (
    make_execution_config,
    make_graph_snapshot,
    make_interrupt,
    make_mock_request_context,
)


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


def make_interrupted_snapshot(value="Need input"):
    return make_graph_snapshot(interrupts=[make_interrupt(id="i-1", value=value)], next=("ask",))


class TestStream:
    async def test_stream_yields_stream_events_and_completion(self):
        stream_event = TaskStatusUpdateEvent(
            task_id="task-1",
            context_id="ctx-1",
            status={"state": TaskState.TASK_STATE_WORKING},
        )
        graph = Mock()
        graph.astream.return_value = make_astream(("custom", object()))
        graph.aget_state = AsyncMock(return_value=make_graph_snapshot())
        executor = LangGraphExecutor(compiled_graph=graph, config=Mock())

        with patch(
            "aion.langgraph.server.execution.event_converter.LangGraphA2AConverter.convert",
            return_value=[stream_event],
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
        graph.aget_state = AsyncMock(return_value=make_graph_snapshot())
        executor = LangGraphExecutor(compiled_graph=graph, config=Mock())

        with patch.object(
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
        graph.aget_state = AsyncMock(return_value=make_interrupted_snapshot())
        executor = LangGraphExecutor(compiled_graph=graph, config=Mock())

        with patch.object(
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

        with patch.object(executor, "_graph_state", new=AsyncMock(return_value=make_graph_snapshot())), \
             patch.object(executor, "stream", return_value=make_astream("event")) as stream:
            events = [event async for event in executor.resume(make_context(), make_config())]

        assert events == ["event"]
        stream.assert_called_once()

    async def test_resume_non_interrupted_state_without_input_raises_execution_error(self):
        executor = LangGraphExecutor(compiled_graph=Mock(), config=Mock())
        context = make_context()
        context.get_user_input.return_value = ""

        with patch.object(executor, "_graph_state", new=AsyncMock(return_value=make_graph_snapshot())):
            with pytest.raises(ExecutionError):
                [event async for event in executor.resume(context, make_config())]


class TestGraphStateAndFinalize:
    async def test_graph_state_returns_the_langgraph_snapshot(self):
        graph_snapshot = make_graph_snapshot()
        graph = Mock()
        graph.aget_state = AsyncMock(return_value=graph_snapshot)
        executor = LangGraphExecutor(compiled_graph=graph, config=Mock())

        result = await executor._graph_state(make_config())

        assert result is graph_snapshot

    async def test_graph_state_requires_context_id(self):
        executor = LangGraphExecutor(compiled_graph=Mock(), config=Mock())

        with pytest.raises(ValueError):
            await executor._graph_state(make_config(context_id=None))

    async def test_graph_state_wraps_graph_errors(self):
        graph = Mock()
        graph.aget_state = AsyncMock(side_effect=RuntimeError("state failed"))
        executor = LangGraphExecutor(compiled_graph=graph, config=Mock())

        with pytest.raises(StateRetrievalError):
            await executor._graph_state(make_config())

    async def test_graph_state_refuses_state_saved_under_the_context_alone(self):
        """An empty scoped thread beside a legacy one is refused, not read as empty."""
        graph = Mock()
        graph.aget_state = AsyncMock(side_effect=[
            make_graph_snapshot(values={}),
            make_graph_snapshot(values={"messages": ["legacy"]}),
        ])
        executor = LangGraphExecutor(compiled_graph=graph, config=Mock())

        with pytest.raises(StateRetrievalError) as raised:
            await executor._graph_state(make_config())

        assert isinstance(raised.value.__cause__, LegacyStateError)
        legacy_config = graph.aget_state.call_args_list[1].args[0]
        assert legacy_config == {"configurable": {"thread_id": "ctx-1"}}

    async def test_finalize_emits_interrupt_for_an_interrupted_graph(self):
        executor = LangGraphExecutor(compiled_graph=Mock(), config=Mock())
        interrupted = make_interrupted_snapshot(value="Need input")

        with patch.object(executor, "_graph_state", new=AsyncMock(return_value=interrupted)), \
             patch.object(executor._result_handler, "handle", return_value=[]) as handle:
            events = [
                event
                async for event in executor._finalize(
                    StreamResult(),
                    make_config(),
                    make_context(),
                    converter=LangGraphA2AConverter(task_id="task-1", context_id="ctx-1"),
                )
            ]

        assert handle.call_args.args[1] is interrupted
        assert events[-1].status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        assert events[-1].status.message.parts[0].text == "Need input"
        assert events[-1].status.message.metadata["interruptId"] == "i-1"

    async def test_finalize_closes_an_open_section_before_the_result_and_the_completion(self):
        """The section a model call left open ends with its text, then the outbox answer, then COMPLETED."""
        executor = LangGraphExecutor(compiled_graph=Mock(), config=Mock())
        converter = LangGraphA2AConverter(task_id="task-1", context_id="ctx-1")
        converter.convert("messages", AIMessageChunk(content="It is ", id="run-1"))
        converter.convert("messages", AIMessageChunk(content="sunny.", id="run-1"))
        outbox = A2AOutbox(message=Message(message_id="m-1", role=Role.ROLE_AGENT, parts=[Part(text="Done.")]))

        with patch.object(executor, "_graph_state", new=AsyncMock(return_value=make_graph_snapshot())):
            events = [
                event
                async for event in executor._finalize(
                    StreamResult(outbox=outbox), make_config(), make_context(), converter=converter
                )
            ]

        final, result, terminal = events
        assert final.artifact.artifact_id == ArtifactId.STREAM_DELTA.value
        assert [part.text for part in final.artifact.parts] == ["It is sunny."]
        assert (final.append, final.last_chunk) == (False, True)
        assert isinstance(result, Message)
        assert terminal.status.state == TaskState.TASK_STATE_COMPLETED

    async def test_finalize_closes_an_open_section_before_an_interrupt(self):
        """What the graph streamed before stopping for input ends as the section's text, not a message."""
        executor = LangGraphExecutor(compiled_graph=Mock(), config=Mock())
        converter = LangGraphA2AConverter(task_id="task-1", context_id="ctx-1")
        converter.convert("messages", AIMessageChunk(content="I need to know whether ", id="run-1"))

        with patch.object(executor, "_graph_state", new=AsyncMock(return_value=make_interrupted_snapshot())):
            events = [
                event
                async for event in executor._finalize(StreamResult(), make_config(), make_context(), converter=converter)
            ]

        final, terminal = events
        assert [part.text for part in final.artifact.parts] == ["I need to know whether "]
        assert final.last_chunk is True
        assert terminal.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        assert terminal.status.message.parts[0].text == "Need input"

    async def test_finalize_without_an_open_section_adds_no_stream_delta(self):
        executor = LangGraphExecutor(compiled_graph=Mock(), config=Mock())
        converter = LangGraphA2AConverter(task_id="task-1", context_id="ctx-1")

        with patch.object(executor, "_graph_state", new=AsyncMock(return_value=make_graph_snapshot())):
            events = [
                event
                async for event in executor._finalize(StreamResult(), make_config(), make_context(), converter=converter)
            ]

        assert len(events) == 1
        assert events[0].status.state == TaskState.TASK_STATE_COMPLETED
        assert not events[0].status.HasField("message")

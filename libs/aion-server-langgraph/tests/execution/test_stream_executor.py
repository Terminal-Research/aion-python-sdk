from unittest.mock import AsyncMock, Mock, patch

import pytest
from a2a.types import Artifact, Part, TaskArtifactUpdateEvent, TaskState, TaskStatus, TaskStatusUpdateEvent
from aion.shared.types import ArtifactId

from aion.server_langgraph.execution.event_converter import LangGraphA2AConverter
from aion.server_langgraph.execution.stream_executor import StreamExecutor

TASK_ID = "task-1"
CONTEXT_ID = "ctx-1"


async def make_astream(*events):
    """Async generator yielding (event_type, event_data) tuples."""
    for event in events:
        yield event


def make_stream_delta_event(text="chunk"):
    """Create a TaskArtifactUpdateEvent with STREAM_DELTA artifact."""
    return TaskArtifactUpdateEvent(
        task_id=TASK_ID,
        context_id=CONTEXT_ID,
        artifact=Artifact(
            artifact_id=ArtifactId.STREAM_DELTA.value,
            name="Stream Delta",
            parts=[Part(text=text)],
        ),
        append=False,
        last_chunk=False,
    )


def make_status_event_with_message(text="response"):
    """Create a TaskStatusUpdateEvent with a message set."""
    from a2a.types import Message, Role
    msg = Message(
        task_id=TASK_ID,
        context_id=CONTEXT_ID,
        message_id="msg-1",
        role=Role.ROLE_AGENT,
        parts=[Part(text=text)],
    )
    return TaskStatusUpdateEvent(
        task_id=TASK_ID,
        context_id=CONTEXT_ID,
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING, message=msg),
    )


def make_executor(converter=None, graph=None, preprocessor=None):
    """Create a StreamExecutor with optional mocks."""
    if converter is None:
        converter = Mock(spec=LangGraphA2AConverter)
        converter.convert.return_value = []
    if graph is None:
        graph = Mock()
        graph.astream.return_value = make_astream()
    return StreamExecutor(compiled_graph=graph, converter=converter, preprocessor=preprocessor)


class TestTrack:
    """_track() accumulates delta_text from STREAM_DELTA and resets on full messages."""

    def test_stream_delta_with_text_accumulates_delta_text(self):
        """Text parts in a STREAM_DELTA artifact are concatenated into delta_text."""
        executor = make_executor()
        executor._track(make_stream_delta_event("Hello "))
        executor._track(make_stream_delta_event("world"))
        assert executor.result.delta_text == "Hello world"

    def test_stream_delta_without_text_parts_leaves_delta_text_unchanged(self):
        """STREAM_DELTA artifact with no text parts does not change delta_text."""
        executor = make_executor()
        executor._delta_text = "existing"
        event = TaskArtifactUpdateEvent(
            task_id=TASK_ID,
            context_id=CONTEXT_ID,
            artifact=Artifact(
                artifact_id=ArtifactId.STREAM_DELTA.value,
                name="Stream Delta",
                parts=[Part(url="https://img.png")],
            ),
            append=False,
            last_chunk=False,
        )
        executor._track(event)
        assert executor.result.delta_text == "existing"

    def test_non_stream_delta_artifact_does_not_affect_delta_text(self):
        """Artifact with a different artifact_id is ignored by delta tracking."""
        executor = make_executor()
        executor._delta_text = "accumulated"
        event = TaskArtifactUpdateEvent(
            task_id=TASK_ID,
            context_id=CONTEXT_ID,
            artifact=Artifact(
                artifact_id="other-artifact",
                name="Other",
                parts=[Part(text="noise")],
            ),
            append=False,
            last_chunk=True,
        )
        executor._track(event)
        assert executor.result.delta_text == "accumulated"

    def test_status_event_with_message_resets_delta_text(self):
        """A TaskStatusUpdateEvent with message set clears the accumulated delta_text."""
        executor = make_executor()
        executor._delta_text = "partial text"
        executor._track(make_status_event_with_message())
        assert executor.result.delta_text == ""


class TestExecute:
    """execute() streams graph events through converter and yields A2A events."""

    async def test_yields_a2a_events_from_converter(self):
        """Each event returned by converter.convert is yielded to the caller."""
        a2a_event = make_stream_delta_event("hello")
        graph = Mock()
        graph.astream.return_value = make_astream(("custom", object()))
        converter = Mock(spec=LangGraphA2AConverter)
        converter.convert.return_value = [a2a_event]
        executor = StreamExecutor(compiled_graph=graph, converter=converter)

        collected = [e async for e in executor.execute({}, {})]
        assert collected == [a2a_event]

    async def test_messages_event_data_is_unpacked(self):
        """For 'messages' events, event_data tuple is unpacked before converter call."""
        msg = Mock()
        graph = Mock()
        graph.astream.return_value = make_astream(("messages", (msg, {"metadata": True})))
        converter = Mock(spec=LangGraphA2AConverter)
        converter.convert.return_value = []
        executor = StreamExecutor(compiled_graph=graph, converter=converter)

        [e async for e in executor.execute({}, {})]
        # converter should receive the unwrapped message, not the tuple
        converter.convert.assert_called_once_with("messages", msg)

    async def test_preprocessor_is_called_for_each_event(self):
        """If preprocessor is set, process() is called with each raw event."""
        graph = Mock()
        graph.astream.return_value = make_astream(("values", {"state": 1}), ("updates", {"node": {}}))
        converter = Mock(spec=LangGraphA2AConverter)
        converter.convert.return_value = []
        preprocessor = Mock()
        executor = StreamExecutor(compiled_graph=graph, converter=converter, preprocessor=preprocessor)

        [e async for e in executor.execute({}, {})]
        assert preprocessor.process.call_count == 2

    async def test_preprocessor_not_set_does_not_raise(self):
        """Missing preprocessor is handled gracefully."""
        graph = Mock()
        graph.astream.return_value = make_astream(("values", {}))
        converter = Mock(spec=LangGraphA2AConverter)
        converter.convert.return_value = []
        executor = StreamExecutor(compiled_graph=graph, converter=converter, preprocessor=None)

        # should not raise
        [e async for e in executor.execute({}, {})]

    async def test_runtime_context_passed_to_astream(self):
        """runtime_context is forwarded to astream as the 'context' kwarg."""
        graph = Mock()
        graph.astream.return_value = make_astream()
        converter = Mock(spec=LangGraphA2AConverter)
        executor = StreamExecutor(compiled_graph=graph, converter=converter)
        runtime_ctx = object()

        [e async for e in executor.execute({}, {}, runtime_context=runtime_ctx)]
        _, kwargs = graph.astream.call_args
        assert kwargs.get("context") is runtime_ctx

    async def test_no_runtime_context_omits_context_kwarg(self):
        """Without runtime_context, 'context' kwarg is not passed to astream."""
        graph = Mock()
        graph.astream.return_value = make_astream()
        converter = Mock(spec=LangGraphA2AConverter)
        executor = StreamExecutor(compiled_graph=graph, converter=converter)

        [e async for e in executor.execute({}, {})]
        _, kwargs = graph.astream.call_args
        assert "context" not in kwargs

    async def test_delta_text_accumulates_across_stream_delta_events(self):
        """delta_text in result reflects text from all STREAM_DELTA events."""
        event1 = make_stream_delta_event("Hello ")
        event2 = make_stream_delta_event("world")
        graph = Mock()
        graph.astream.return_value = make_astream(("custom", None), ("custom", None))
        converter = Mock(spec=LangGraphA2AConverter)
        converter.convert.side_effect = [[event1], [event2]]
        executor = StreamExecutor(compiled_graph=graph, converter=converter)

        [e async for e in executor.execute({}, {})]
        assert executor.result.delta_text == "Hello world"

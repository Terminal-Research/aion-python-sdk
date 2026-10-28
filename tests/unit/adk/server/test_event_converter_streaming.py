"""What an ADK model's SSE stream becomes on the wire.

Under ``StreamingMode.SSE`` a model call yields partial events, one per
chunk, and then one non-partial event with the whole answer. The partial
ones are the chunks of a section of ``aion:stream-delta`` the client renders
as they come; the non-partial one ends the section with the end mark - one
empty part with ``lastChunk`` - and its text is not sent again. The next
model call opens a section of its own. The streamed text is the
artifact, never a message. A thought goes to ``aion:thinking-delta`` and
leaves the section open.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from a2a.types import TaskArtifactUpdateEvent, TaskState, TaskStatusUpdateEvent
from google.adk.events import Event
from google.genai import types

from aion.adk.authoring.invocation import emit_thinking
from aion.adk.server.execution.adk_executor import ADKExecutor
from aion.adk.server.execution.event_converter import ADKToA2AEventConverter
from aion.adk.server.execution.stream_executor import ADKStreamResult
from aion.core.a2a import ArtifactId


def _event(*parts: types.Part, partial: bool, custom_metadata: dict | None = None) -> Event:
    return Event(
        author="agent",
        content=types.Content(role="model", parts=list(parts)),
        partial=partial,
        custom_metadata=custom_metadata,
    )


def _text(text: str, *, partial: bool = True, custom_metadata: dict | None = None) -> Event:
    return _event(types.Part(text=text), partial=partial, custom_metadata=custom_metadata)


def _thought(text: str, metadata: dict | None = None) -> Event:
    emitted: list[Event] = []
    emit_thinking(emitted.append, text, metadata=metadata)
    return emitted[0]


def _converter() -> ADKToA2AEventConverter:
    return ADKToA2AEventConverter(task_id="task-1", context_id="ctx-1")


async def _convert(*events: Event, converter: ADKToA2AEventConverter | None = None) -> list:
    converter = converter or _converter()
    converted = []
    for event in events:
        converted += await converter.convert(event)
    return converted


def _deltas(events) -> list[tuple[str, bool, bool]]:
    """Each aion:stream-delta event as (text, append, last_chunk)."""
    return [
        ("".join(part.text for part in event.artifact.parts), event.append, event.last_chunk)
        for event in events
        if isinstance(event, TaskArtifactUpdateEvent)
        and event.artifact.artifact_id == ArtifactId.STREAM_DELTA.value
    ]


def _thoughts(events) -> list[TaskArtifactUpdateEvent]:
    return [
        event
        for event in events
        if isinstance(event, TaskArtifactUpdateEvent)
        and event.artifact.artifact_id == ArtifactId.THINKING_DELTA.value
    ]


def _messages(events) -> list[str]:
    return [
        "".join(part.text for part in event.status.message.parts)
        for event in events
        if isinstance(event, TaskStatusUpdateEvent) and event.status.HasField("message")
    ]


async def test_partial_events_stream_and_the_aggregate_event_ends_the_section() -> None:
    events = await _convert(_text("It "), _text("is sunny."), _text("It is sunny.", partial=False))

    assert _deltas(events) == [("It ", False, False), ("is sunny.", True, False), ("", True, True)]
    assert _messages(events) == []


async def test_the_end_mark_is_one_empty_part_without_metadata() -> None:
    events = await _convert(_text("It "), _text("is sunny."), _text("It is sunny.", partial=False))

    end = events[-1]
    assert [part.text for part in end.artifact.parts] == [""]
    assert not end.artifact.HasField("metadata")
    assert end.artifact.name == "Stream Delta"


async def test_the_aggregate_text_is_not_sent_again() -> None:
    """What the chunks said is the section; the framework's aggregate only ends it."""
    events = await _convert(_text("It "), _text("It is sunny.", partial=False))

    assert _deltas(events) == [("It ", False, False), ("", True, True)]


async def test_every_chunk_carries_the_agents_metadata() -> None:
    events = await _convert(
        _text("It ", custom_metadata={"lane": "weather"}),
        _text("is sunny.", custom_metadata={"lane": "weather"}),
        _text("It is sunny.", partial=False),
    )

    assert [dict(event.artifact.metadata) for event in events[:2]] == [{"lane": "weather"}] * 2


async def test_a_non_text_part_of_the_aggregate_event_is_still_a_message() -> None:
    image = types.Part(inline_data=types.Blob(data=b"png-bytes", mime_type="image/png"))
    events = await _convert(_text("Here it is."), _event(types.Part(text="Here it is."), image, partial=False))

    assert _deltas(events) == [("Here it is.", False, False), ("", True, True)]
    status = [event for event in events if isinstance(event, TaskStatusUpdateEvent)]
    assert len(status) == 1
    assert [part.WhichOneof("content") for part in status[0].status.message.parts] == ["raw"]


async def test_an_aggregate_event_without_text_ends_the_section() -> None:
    call = types.Part(function_call=types.FunctionCall(name="get_weather", args={}))
    events = await _convert(_text("Checking"), _text("..."), _event(call, partial=False))

    assert _deltas(events)[-1] == ("", True, True)
    assert _messages(events) == []


async def test_the_next_model_call_opens_a_new_section() -> None:
    events = await _convert(
        _text("Checking."),
        _event(
            types.Part(text="Checking."),
            types.Part(function_call=types.FunctionCall(name="get_weather", args={})),
            partial=False,
        ),
        _text("Sunny."),
    )

    assert _deltas(events) == [("Checking.", False, False), ("", True, True), ("Sunny.", False, False)]
    assert _messages(events) == []


async def test_a_reply_that_did_not_stream_is_a_message() -> None:
    events = await _convert(_text("It is sunny.", partial=False))

    assert _messages(events) == ["It is sunny."]
    assert events[0].status.state == TaskState.TASK_STATE_WORKING
    assert _deltas(events) == []


async def test_a_reply_after_a_closed_section_is_a_message() -> None:
    events = await _convert(
        _text("It is sunny."),
        _text("It is sunny.", partial=False),
        _text("Anything else?", partial=False),
    )

    assert _deltas(events) == [("It is sunny.", False, False), ("", True, True)]
    assert _messages(events) == ["Anything else?"]


async def test_a_thought_is_one_text_part_of_the_thinking_artifact() -> None:
    events = await _convert(_thought("Looking up the forecast", metadata={"step": "lookup"}))

    assert len(events) == 1
    thought = _thoughts(events)[0]
    assert [part.text for part in thought.artifact.parts] == ["Looking up the forecast"]
    assert thought.artifact.name == "Thinking Delta"
    assert thought.artifact.metadata["step"] == "lookup"
    assert thought.append is True
    assert thought.last_chunk is False


async def test_a_thought_without_text_produces_nothing() -> None:
    assert await _convert(_thought("")) == []


async def test_a_thought_does_not_end_the_open_section() -> None:
    events = await _convert(
        _text("It "),
        _thought("Looking up the forecast"),
        _text("is sunny."),
        _text("It is sunny.", partial=False),
    )

    assert _deltas(events) == [("It ", False, False), ("is sunny.", True, False), ("", True, True)]
    assert len(_thoughts(events)) == 1
    assert events[1].artifact.artifact_id == ArtifactId.THINKING_DELTA.value
    assert _messages(events) == []


async def test_finalize_stream_closes_an_open_section_with_the_end_mark() -> None:
    converter = _converter()
    await _convert(_text("It ", custom_metadata={"lane": "weather"}), _text("is sunny."), converter=converter)

    events = converter.finalize_stream()

    assert _deltas(events) == [("", True, True)]
    assert _messages(events) == []


async def test_finalize_stream_without_an_open_section_produces_nothing() -> None:
    converter = _converter()
    await _convert(_text("It is sunny."), _text("It is sunny.", partial=False), converter=converter)

    assert converter.finalize_stream() == []
    assert _converter().finalize_stream() == []


async def test_the_executor_closes_the_section_before_the_terminal_status() -> None:
    converter = _converter()
    await _convert(_text("It "), _text("is sunny."), converter=converter)
    executor = ADKExecutor(agent=object(), config=MagicMock(), session_service=MagicMock(), artifact_service=MagicMock())
    session = MagicMock()
    session.state = {}

    events = [
        event
        async for event in executor._finalize(
            ADKStreamResult(), converter, session, MagicMock(), "task-1", "ctx-1"
        )
    ]

    assert _deltas(events) == [("", True, True)]
    assert events[-1].status.state == TaskState.TASK_STATE_COMPLETED
    assert not events[-1].status.HasField("message")
    assert len(events) == 2


async def test_a_thread_streaming_an_iterator_ends_with_the_end_mark_and_no_message() -> None:
    """Thread.post() with an async iterator, through the converter as the executor would carry it."""
    from aion.adk.authoring.invocation.thread import Thread
    from aion.core.a2a.extensions.messaging import MessageActionPayload

    emitted: list[Event] = []
    thread = Thread(
        context=MagicMock(),
        context_id="C123",
        parent_context_id=None,
        network="Slack",
        default_reply_target="C123",
    )

    async def answer():
        yield "It "
        yield "is sunny."

    routing = MessageActionPayload(trajectory="conversation", context_id="C123")
    with patch("aion.adk.authoring.invocation.thread.get_adk_emitter", return_value=emitted.append):
        await thread.post(answer(), target=routing)
    converter = _converter()
    events = await _convert(*emitted, converter=converter)

    assert _deltas(events) == [("It ", False, False), ("is sunny.", True, False), ("", True, True)]
    assert _messages(events) == []
    assert converter.finalize_stream() == []


async def test_a_partial_thought_streams_nothing() -> None:
    events = await _convert(_event(types.Part(text="hmm", thought=True), partial=True))

    assert events == []


async def test_a_function_response_is_not_a_message() -> None:
    response = _event(
        types.Part(function_response=types.FunctionResponse(name="get_weather", response={"result": "sunny"})),
        partial=False,
    )

    assert _messages(await _convert(response)) == []

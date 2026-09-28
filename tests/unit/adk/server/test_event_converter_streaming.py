"""What an ADK model's SSE stream becomes on the wire.

Under ``StreamingMode.SSE`` a model call yields partial events, one per
chunk, and then one non-partial event with the whole answer. The partial
ones are stream deltas the client renders as they come; the non-partial one
is the durable message, and it ends the stream section so the next model
call opens its own.
"""

from __future__ import annotations

from a2a.types import TaskArtifactUpdateEvent, TaskStatusUpdateEvent
from google.adk.events import Event
from google.genai import types

from aion.adk.server.execution.event_converter import ADKToA2AEventConverter
from aion.core.a2a import ArtifactId


def _event(*parts: types.Part, partial: bool) -> Event:
    return Event(author="agent", content=types.Content(role="model", parts=list(parts)), partial=partial)


def _text(text: str, *, partial: bool = True) -> Event:
    return _event(types.Part(text=text), partial=partial)


async def _convert(*events: Event) -> list:
    converter = ADKToA2AEventConverter(task_id="task-1", context_id="ctx-1")
    converted = []
    for event in events:
        converted += await converter.convert(event)
    return converted


def _deltas(events) -> list[tuple[str, bool]]:
    return [
        ("".join(part.text for part in event.artifact.parts), event.append)
        for event in events
        if isinstance(event, TaskArtifactUpdateEvent)
        and event.artifact.artifact_id == ArtifactId.STREAM_DELTA.value
    ]


def _messages(events) -> list[str]:
    return [
        "".join(part.text for part in event.status.message.parts)
        for event in events
        if isinstance(event, TaskStatusUpdateEvent) and event.status.HasField("message")
    ]


async def test_partial_events_stream_and_the_final_one_is_the_message() -> None:
    events = await _convert(_text("It "), _text("is sunny."), _text("It is sunny.", partial=False))

    assert _deltas(events) == [("It ", False), ("is sunny.", True)]
    assert _messages(events) == ["It is sunny."]


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

    assert _deltas(events) == [("Checking.", False), ("Sunny.", False)]
    assert _messages(events) == ["Checking."]


async def test_a_partial_thought_streams_nothing() -> None:
    events = await _convert(_event(types.Part(text="hmm", thought=True), partial=True))

    assert events == []


async def test_a_function_response_is_not_a_message() -> None:
    response = _event(
        types.Part(function_response=types.FunctionResponse(name="get_weather", response={"result": "sunny"})),
        partial=False,
    )

    assert _messages(await _convert(response)) == []

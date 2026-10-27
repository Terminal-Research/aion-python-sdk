"""Normalize an A2A stream into a flat list of events.

The recorder filters nothing and reorders nothing: what the server sent is
what the assertions see. Anything that reads a protobuf field lives here, so
the scenarios stay written in terms of kinds, texts and states.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterable, Optional, Sequence

from a2a.types.a2a_pb2 import (
    Artifact,
    Message,
    StreamResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatusUpdateEvent,
)
from google.protobuf.json_format import MessageToDict

__all__ = [
    "ASSISTANT_ANSWER_ARTIFACT_ID",
    "Ev",
    "REACTION_ARTIFACT_ID",
    "STREAM_DELTA_ARTIFACT_ID",
    "TERMINAL_STATES",
    "THINKING_DELTA_ARTIFACT_ID",
    "artifact_updates",
    "data_of_parts",
    "final_task",
    "is_chunk",
    "is_section_end",
    "record_stream",
    "replies",
    "reply_texts",
    "section_texts",
    "stored_artifact",
    "stored_messages",
    "stored_texts",
    "text_of_parts",
    "texts_of_parts",
    "thought_texts",
    "to_event",
]

# The artifacts the SDK fills itself, under ids it reserves. The task stores
# each of them like any other artifact, and a push subscriber receives them.
STREAM_DELTA_ARTIFACT_ID = "aion:stream-delta"
"""The answer the model streamed: the chunks of a section, then its whole text."""

THINKING_DELTA_ARTIFACT_ID = "aion:thinking-delta"
"""The agent's thoughts: one text part per thought, for the whole task."""

REACTION_ARTIFACT_ID = "aion:reaction"
"""The agent's latest reaction to a message."""

ASSISTANT_ANSWER_ARTIFACT_ID = "aion:assistant-answer"
"""Every part of the last message of a task that completed with no other result."""

TERMINAL_STATES = ("COMPLETED", "FAILED", "CANCELED", "REJECTED")
"""States a task does not leave. INPUT_REQUIRED is not one: it resumes."""


def _state_name(state: int) -> Optional[str]:
    """Task state as its short name, e.g. ``WORKING``."""
    if not state:
        return None
    try:
        return TaskState.Name(state).removeprefix("TASK_STATE_")
    except ValueError:
        return str(state)


def text_of_parts(parts: Iterable[Any]) -> str:
    """Concatenate the text parts of a message or artifact."""
    return "".join(part.text for part in parts if part.text)


def texts_of_parts(parts: Iterable[Any]) -> tuple[str, ...]:
    """The text of each text part of a message or artifact, in order."""
    return tuple(part.text for part in parts if part.text)


def data_of_parts(parts: Iterable[Any]) -> list[Any]:
    """The value of each data part of a message or artifact, in order, as plain JSON."""
    return [MessageToDict(part.data) for part in parts if part.HasField("data")]


def _struct_to_dict(struct: Any) -> dict:
    """A protobuf Struct as a plain dict."""
    if struct is None:
        return {}
    return MessageToDict(struct) if struct.ListFields() else {}


@dataclass(frozen=True)
class Ev:
    """One event of a stream, in the terms the scenarios assert on.

    Attributes:
        kind: ``task``, ``message``, ``status`` or ``artifact``.
        state: Task state name for ``task`` and ``status``, else None.
        final: True for the terminal ``task`` event that closes the stream.
        text: Concatenated text the event carries.
        texts: The text of each text part, in order - one thought each on
            a thinking-delta update.
        artifact_id: Id of the artifact an ``artifact`` event updates.
        artifact_name: Its name.
        append: The artifact update's append flag.
        last_chunk: The artifact update's last-chunk flag.
        task_id: Task the event belongs to, when it names one.
        context_id: Context the event belongs to, when it names one.
        metadata: Event metadata as a plain dict. The schema marker of the
            messaging extension lives here, on the update rather than on
            the artifact.
        artifact_metadata: The artifact's own metadata, which describes the
            artifact itself - its ``status`` and ``status_reason``. A
            different dict from ``metadata``, and the spec means it to be.
        raw: The protobuf message itself, for anything not projected here.
    """

    kind: str
    state: Optional[str] = None
    final: bool = False
    text: str = ""
    texts: tuple[str, ...] = ()
    artifact_id: Optional[str] = None
    artifact_name: Optional[str] = None
    append: bool = False
    last_chunk: bool = False
    task_id: Optional[str] = None
    context_id: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    artifact_metadata: dict = field(default_factory=dict)
    raw: Any = None

    def __repr__(self) -> str:
        parts = [self.kind]
        if self.state:
            parts.append(self.state)
        if self.artifact_id:
            parts.append(f"artifact={self.artifact_name or self.artifact_id}")
            parts.append(f"append={self.append} last={self.last_chunk}")
        if self.text:
            preview = self.text if len(self.text) <= 60 else f"{self.text[:57]}..."
            parts.append(repr(preview))
        return f"Ev({' '.join(parts)})"


def to_event(payload: Any) -> Ev:
    """Project one protobuf stream payload onto an ``Ev``."""
    if isinstance(payload, Task):
        state = _state_name(payload.status.state)
        return Ev(
            kind="task",
            state=state,
            final=state in TERMINAL_STATES or state == "INPUT_REQUIRED",
            text=text_of_parts(payload.status.message.parts) if payload.status.HasField("message") else "",
            texts=texts_of_parts(payload.status.message.parts) if payload.status.HasField("message") else (),
            task_id=payload.id or None,
            context_id=payload.context_id or None,
            metadata=_struct_to_dict(payload.metadata),
            raw=payload,
        )

    if isinstance(payload, Message):
        return Ev(
            kind="message",
            text=text_of_parts(payload.parts),
            texts=texts_of_parts(payload.parts),
            task_id=payload.task_id or None,
            context_id=payload.context_id or None,
            metadata=_struct_to_dict(payload.metadata),
            raw=payload,
        )

    if isinstance(payload, TaskStatusUpdateEvent):
        return Ev(
            kind="status",
            state=_state_name(payload.status.state),
            text=text_of_parts(payload.status.message.parts) if payload.status.HasField("message") else "",
            texts=texts_of_parts(payload.status.message.parts) if payload.status.HasField("message") else (),
            task_id=payload.task_id or None,
            context_id=payload.context_id or None,
            metadata=_struct_to_dict(payload.metadata),
            raw=payload,
        )

    if isinstance(payload, TaskArtifactUpdateEvent):
        artifact: Artifact = payload.artifact
        return Ev(
            kind="artifact",
            text=text_of_parts(artifact.parts),
            texts=texts_of_parts(artifact.parts),
            artifact_id=artifact.artifact_id or None,
            artifact_name=artifact.name or None,
            append=bool(payload.append),
            last_chunk=bool(payload.last_chunk),
            artifact_metadata=_struct_to_dict(artifact.metadata),
            task_id=payload.task_id or None,
            context_id=payload.context_id or None,
            metadata=_struct_to_dict(payload.metadata),
            raw=payload,
        )

    raise TypeError(f"Unrecognized stream payload: {type(payload).__name__}")


def _payload_of(response: StreamResponse) -> Any:
    """The one field a StreamResponse carries."""
    for name in ("task", "message", "status_update", "artifact_update"):
        if response.HasField(name):
            return getattr(response, name)
    raise ValueError(f"StreamResponse carries no payload: {response}")


async def record_stream(stream: AsyncIterator[StreamResponse]) -> list[Ev]:
    """Drain a stream into the events it delivered, in order."""
    return [to_event(_payload_of(response)) async for response in stream]


def final_task(events: Sequence[Ev]) -> Ev:
    """The Task event a stream closes with.

    Every turn ends with one - the state the caller is told to act on - so a
    stream without it is itself the finding, and this says so rather than
    raising an IndexError three lines later.
    """
    for event in reversed(events):
        if event.kind == "task":
            return event
    raise AssertionError(f"stream carries no Task event: {list(events)}")


def replies(events: Sequence[Ev]) -> list[Ev]:
    """The messages the agent sent this turn: the statuses that carry one."""
    return [event for event in events if event.kind == "status" and event.text]


def reply_texts(events: Sequence[Ev]) -> list[str]:
    """The text of each message the agent sent this turn, in order."""
    return [event.text for event in replies(events)]


def artifact_updates(events: Sequence[Ev], artifact_id: str) -> list[Ev]:
    """The updates of one artifact this turn, in order."""
    return [event for event in events if event.kind == "artifact" and event.artifact_id == artifact_id]


def is_chunk(event: Ev) -> bool:
    """True for one chunk of a streamed section: a stream-delta update without ``lastChunk``."""
    return event.kind == "artifact" and event.artifact_id == STREAM_DELTA_ARTIFACT_ID and not event.last_chunk


def is_section_end(event: Ev) -> bool:
    """True for the stream-delta update that ends a section with its whole text (``lastChunk`` set)."""
    return event.kind == "artifact" and event.artifact_id == STREAM_DELTA_ARTIFACT_ID and event.last_chunk


def section_texts(events: Sequence[Ev]) -> list[str]:
    """The whole text of each streamed section that ended this turn, in order.

    This is what the model said by streaming: a section's chunks show it
    growing, and the update that ends the section carries all of it.
    """
    return [event.text for event in events if is_section_end(event)]


def thought_texts(events: Sequence[Ev]) -> list[str]:
    """Every thought delivered this turn, in order: one text part of a thinking-delta update each."""
    return [text for event in artifact_updates(events, THINKING_DELTA_ARTIFACT_ID) for text in event.texts]


def stored_messages(task: Task) -> list[Message]:
    """Every message of a stored task, in order, each once.

    A terminal task ends its history with its final message and keeps it on
    ``status.message`` as well. A running or interrupted task has its current
    message only on ``status.message``, until the next message moves it into
    history - reading history alone would read that task one message short.
    So the status message is added unless the history already ends with it.
    """
    messages = list(task.history)
    if not task.status.HasField("message"):
        return messages
    current = task.status.message
    if messages:
        last = messages[-1]
        if last.message_id or current.message_id:
            same = last.message_id == current.message_id
        else:
            same = last == current
        if same:
            return messages
    messages.append(current)
    return messages


def stored_texts(task: Task) -> list[str]:
    """Everything a stored task says, in order, each message once (``stored_messages``)."""
    return [part.text for message in stored_messages(task) for part in message.parts if part.text]


def stored_artifact(task: Task, artifact_id: str) -> Optional[Artifact]:
    """The artifact a stored task holds under this id, or None."""
    return next((artifact for artifact in task.artifacts if artifact.artifact_id == artifact_id), None)

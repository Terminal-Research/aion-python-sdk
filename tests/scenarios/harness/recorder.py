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
    "EPHEMERAL_KEY",
    "Ev",
    "TERMINAL_STATES",
    "final_task",
    "record_stream",
    "replies",
    "reply_texts",
    "stored_texts",
    "text_of_parts",
    "to_event",
]

EPHEMERAL_KEY = "aion:ephemeral"
"""Metadata flag the server puts on a status update it will not persist."""

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
        artifact_id: Id of the artifact an ``artifact`` event updates.
        artifact_name: Its name.
        append: The artifact update's append flag.
        last_chunk: The artifact update's last-chunk flag.
        ephemeral: True when the server marked this status update ephemeral.
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
    artifact_id: Optional[str] = None
    artifact_name: Optional[str] = None
    append: bool = False
    last_chunk: bool = False
    ephemeral: bool = False
    task_id: Optional[str] = None
    context_id: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    artifact_metadata: dict = field(default_factory=dict)
    raw: Any = None

    def __repr__(self) -> str:
        parts = [self.kind]
        if self.state:
            parts.append(self.state)
        if self.ephemeral:
            parts.append("ephemeral")
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
            task_id=payload.id or None,
            context_id=payload.context_id or None,
            metadata=_struct_to_dict(payload.metadata),
            raw=payload,
        )

    if isinstance(payload, Message):
        return Ev(
            kind="message",
            text=text_of_parts(payload.parts),
            task_id=payload.task_id or None,
            context_id=payload.context_id or None,
            metadata=_struct_to_dict(payload.metadata),
            raw=payload,
        )

    if isinstance(payload, TaskStatusUpdateEvent):
        metadata = _struct_to_dict(payload.metadata)
        return Ev(
            kind="status",
            state=_state_name(payload.status.state),
            text=text_of_parts(payload.status.message.parts) if payload.status.HasField("message") else "",
            ephemeral=bool(metadata.get(EPHEMERAL_KEY)),
            task_id=payload.task_id or None,
            context_id=payload.context_id or None,
            metadata=metadata,
            raw=payload,
        )

    if isinstance(payload, TaskArtifactUpdateEvent):
        artifact: Artifact = payload.artifact
        return Ev(
            kind="artifact",
            text=text_of_parts(artifact.parts),
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
    """The durable replies of a turn: working statuses that carry a message."""
    return [
        event
        for event in events
        if event.kind == "status" and event.text and not event.ephemeral
    ]


def reply_texts(events: Sequence[Ev]) -> list[str]:
    """What the agent said this turn, in order."""
    return [event.text for event in replies(events)]


def stored_texts(task: Task) -> list[str]:
    """Everything a stored task says, in order: its history, then its status.

    A turn's last reply stays on ``status.message`` until the next turn moves
    it into history, so reading history alone reads the task one message
    short - and it is the last message that most scenarios are about.
    """
    texts = [part.text for message in task.history for part in message.parts if part.text]
    if task.status.HasField("message"):
        texts.extend(part.text for part in task.status.message.parts if part.text)
    return texts

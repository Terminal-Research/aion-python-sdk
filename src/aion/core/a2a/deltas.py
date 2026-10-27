"""Events of the artifacts the SDK fills while an agent works.

Two artifacts carry what happens while a task runs, one of each per task:

``aion:stream-delta``
    The answer as the model streams it. While a section streams, its chunks
    arrive with ``append`` set (the first one of a section without it, which
    starts the section over). When the section ends, its whole text arrives
    as one text part with ``append`` unset and ``lastChunk`` set: it replaces
    the chunks, and a later section replaces it in turn. The task keeps the
    last section; the streamed text never becomes a message.

``aion:thinking-delta``
    The agent's thoughts, which only the agent's own code sends. Every thought
    is one more text part of the artifact, for the whole task: a new section
    or a new turn does not start it over. The server decides ``append`` for
    each thought by what the task already holds.

Both are stored in the task and delivered to push subscribers like any other
artifact. Neither is a result of the task.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from a2a.types import Artifact, Part, TaskArtifactUpdateEvent

from aion.core.a2a.enums import ArtifactId, ArtifactName, ArtifactStreamingStatus, ArtifactStreamingStatusReason
from aion.core.constants.a2a import MESSAGING_EXTENSION_URI_V1, STREAM_DELTA_PAYLOAD_SCHEMA_V1

__all__ = [
    "is_stream_delta_chunk",
    "stream_delta_chunk",
    "stream_delta_final",
    "thinking_delta",
]

_STREAM_DELTA_EVENT_METADATA = {MESSAGING_EXTENSION_URI_V1: {"schema": STREAM_DELTA_PAYLOAD_SCHEMA_V1}}
"""The schema marker of a stream-delta event.

The artifact id says which channel this is; the marker on the event says what
shape the payload in it has. The messaging extension names
``artifactUpdate.metadata`` as the marker's place, and a client reading the
stream-delta schema looks for it there.
"""


def stream_delta_chunk(
    *,
    task_id: str,
    context_id: str,
    parts: Sequence[Part],
    append: bool,
    metadata: Optional[Mapping[str, Any]] = None,
) -> TaskArtifactUpdateEvent:
    """One chunk of a streaming section of the answer.

    Args:
        task_id: The task being served.
        context_id: The task's context.
        parts: The chunk's content.
        append: False for the first chunk of a section, which starts the
            artifact over; True for the rest.
        metadata: The agent's own metadata for the artifact.
    """
    artifact_metadata: dict[str, Any] = {
        "status": ArtifactStreamingStatus.ACTIVE.value,
        "status_reason": ArtifactStreamingStatusReason.CHUNK_STREAMING.value,
    }
    if metadata:
        artifact_metadata.update(metadata)
    return TaskArtifactUpdateEvent(
        task_id=task_id,
        context_id=context_id,
        metadata=_STREAM_DELTA_EVENT_METADATA,
        artifact=Artifact(
            artifact_id=ArtifactId.STREAM_DELTA.value,
            name=ArtifactName.STREAM_DELTA.value,
            parts=list(parts),
            metadata=artifact_metadata,
        ),
        append=append,
        last_chunk=False,
    )


def stream_delta_final(
    *,
    task_id: str,
    context_id: str,
    text: str,
    metadata: Optional[Mapping[str, Any]] = None,
) -> TaskArtifactUpdateEvent:
    """The whole text of a section that has ended: one text part that replaces its chunks.

    Args:
        task_id: The task being served.
        context_id: The task's context.
        text: Everything the section said.
        metadata: The agent's own metadata for the artifact.
    """
    artifact_metadata: dict[str, Any] = {"status": ArtifactStreamingStatus.FINALIZED.value}
    if metadata:
        artifact_metadata.update(metadata)
    return TaskArtifactUpdateEvent(
        task_id=task_id,
        context_id=context_id,
        metadata=_STREAM_DELTA_EVENT_METADATA,
        artifact=Artifact(
            artifact_id=ArtifactId.STREAM_DELTA.value,
            name=ArtifactName.STREAM_DELTA.value,
            parts=[Part(text=text)],
            metadata=artifact_metadata,
        ),
        append=False,
        last_chunk=True,
    )


def thinking_delta(
    *,
    task_id: str,
    context_id: str,
    text: str,
    metadata: Optional[Mapping[str, Any]] = None,
) -> TaskArtifactUpdateEvent:
    """One thought of the agent, added to the task's thoughts.

    The event says ``append``; the server unsets it for the task's first
    thought, which creates the artifact.

    Args:
        task_id: The task being served.
        context_id: The task's context.
        text: The thought.
        metadata: The agent's own metadata for the artifact.
    """
    return TaskArtifactUpdateEvent(
        task_id=task_id,
        context_id=context_id,
        artifact=Artifact(
            artifact_id=ArtifactId.THINKING_DELTA.value,
            name=ArtifactName.THINKING_DELTA.value,
            parts=[Part(text=text)],
            metadata=dict(metadata) if metadata else None,
        ),
        append=True,
        last_chunk=False,
    )


def is_stream_delta_chunk(event: object) -> bool:
    """Whether ``event`` is a chunk of a streaming section rather than a section's whole text."""
    return (
        isinstance(event, TaskArtifactUpdateEvent)
        and event.artifact.artifact_id == ArtifactId.STREAM_DELTA.value
        and not event.last_chunk
    )

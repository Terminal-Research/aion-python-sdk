"""Events of the artifacts the SDK fills while an agent works.

Two artifacts carry what happens while a task runs, one of each per task:

``aion:stream-delta``
    The answer as the model streams it. A section's first chunk arrives with
    ``append`` unset, which starts the artifact over; the rest arrive with it
    set. The section ends with ``lastChunk`` set: on its last chunk, when the
    framework marks it as the last, or else on an empty chunk sent after it.
    The text is never sent a second time. When the task stops, the server
    joins the text the task holds into one part (:func:`join_stream_delta`),
    so a later section replaces the earlier one and the task keeps the last.
    The streamed text never becomes a message. A section the agent sends
    somewhere in particular (``Thread.reply`` to a distribution) carries its
    routing target as one more part on its first chunk, and the target stays
    beside the joined text.

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

from aion.core.a2a.enums import ArtifactId, ArtifactName
from aion.core.a2a.extensions.messaging import MessageActionPayload
from aion.core.constants.a2a import MESSAGING_EXTENSION_URI_V1, STREAM_DELTA_PAYLOAD_SCHEMA_V1

__all__ = [
    "join_stream_delta",
    "stream_delta_chunk",
    "stream_delta_end",
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
    last_chunk: bool = False,
    metadata: Optional[Mapping[str, Any]] = None,
    routing: Optional[MessageActionPayload] = None,
) -> TaskArtifactUpdateEvent:
    """One chunk of a streaming section of the answer.

    Args:
        task_id: The task being served.
        context_id: The task's context.
        parts: The chunk's content.
        append: False for the first chunk of a section, which starts the
            artifact over; True for the rest.
        last_chunk: True when the framework marks this chunk as the
            section's last, which then needs no :func:`stream_delta_end`.
        metadata: The agent's own metadata for the artifact.
        routing: The section's routing target, for its first chunk only:
            the parts of a later chunk are added to the artifact, so a
            target there would repeat in the task.
    """
    artifact = Artifact(
        artifact_id=ArtifactId.STREAM_DELTA.value,
        name=ArtifactName.STREAM_DELTA.value,
        parts=list(parts),
        metadata=dict(metadata) if metadata else None,
    )
    return TaskArtifactUpdateEvent(
        task_id=task_id,
        context_id=context_id,
        metadata=_STREAM_DELTA_EVENT_METADATA,
        artifact=routing.route(artifact) if routing is not None else artifact,
        append=append,
        last_chunk=last_chunk,
    )


def stream_delta_end(*, task_id: str, context_id: str) -> TaskArtifactUpdateEvent:
    """The mark that ends a section whose last chunk went out without saying so.

    One empty text part with ``append`` and ``lastChunk`` set: a reader that
    adds every chunk to what it shows adds nothing, and learns the section is
    over.

    Args:
        task_id: The task being served.
        context_id: The task's context.
    """
    return TaskArtifactUpdateEvent(
        task_id=task_id,
        context_id=context_id,
        metadata=_STREAM_DELTA_EVENT_METADATA,
        artifact=Artifact(
            artifact_id=ArtifactId.STREAM_DELTA.value,
            name=ArtifactName.STREAM_DELTA.value,
            parts=[Part(text="")],
        ),
        append=True,
        last_chunk=True,
    )


def join_stream_delta(artifact: Artifact) -> None:
    """Join the text parts of a stream-delta artifact into one, in place.

    The joined text comes first; any other part - a routing target - follows
    it, in the order it had.
    """
    texts = [part.text for part in artifact.parts if part.WhichOneof("content") == "text"]
    if len(texts) < 2 and (not texts or artifact.parts[0].WhichOneof("content") == "text"):
        return
    others = [part for part in artifact.parts if part.WhichOneof("content") != "text"]
    joined: list[Part] = [Part(text="".join(texts))]
    for part in others:
        kept = Part()
        kept.CopyFrom(part)
        joined.append(kept)
    del artifact.parts[:]
    artifact.parts.extend(joined)


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

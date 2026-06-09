"""Helpers for emitting events from LangGraph nodes during invocation.

This module provides helper functions to emit custom events during graph execution.
All events are emitted via LangGraph's custom stream mode.

Architecture (see thread_message_concept.md):
  Builders (aion-core)  →  emit_* (here, explicit params)  →  Thread (magic wrapper)
"""

from typing import Any, Optional

from a2a.types import Artifact
from aion.core.agent.invocation.card import Card
from langchain_core.messages import AIMessage, AIMessageChunk
from langgraph.types import StreamWriter

from aion.core.a2a.extensions.messaging import MessageActionPayload, ReactionActionPayload

from ..events.custom_events import (
    ArtifactCustomEvent,
    CardCustomEvent,
    MessageCustomEvent,
    ReactionCustomEvent,
    TaskUpdateCustomEvent,
)


def emit_artifact(
    writer: StreamWriter,
    artifact: Artifact,
    *,
    routing: MessageActionPayload | None = None,
    append: bool = False,
    is_last_chunk: bool = True,
    metadata: dict | None = None,
) -> None:
    """Emit a pre-built artifact during graph execution.

    The artifact content is determined by the caller — use the framework-agnostic
    builders (file_artifact, data_artifact from aion.core.a2a) to construct it.

    Args:
        writer: LangGraph StreamWriter from node signature
        artifact: Pre-built a2a.types.Artifact to emit
        routing: Optional delivery routing target (MessageActionPayload)
        append: If True, append to previously sent artifact
        is_last_chunk: If True, this is the final chunk
        metadata: Optional user-defined metadata merged into A2A Artifact.metadata.
                  Keys must not start with "aion:" (reserved for service use).

    Example:
        from aion.core.a2a import file_artifact, data_artifact

        def my_node(state: dict, writer: StreamWriter):
            emit_artifact(writer, file_artifact(url="https://example.com/r.pdf", mime_type="application/pdf"))
            emit_artifact(writer, data_artifact({"score": 42}, name="result"), metadata={"owner": "agent-x"})
    """
    writer(ArtifactCustomEvent(
        artifact=artifact,
        append=append,
        is_last_chunk=is_last_chunk,
        routing=routing,
        metadata=metadata,
    ))


def emit_card(
    writer: StreamWriter,
    card: Card,
    *,
    routing: MessageActionPayload | None = None,
    metadata: dict | None = None,
) -> None:
    """Emit a card message during graph execution.

    Produces a TaskStatusUpdateEvent whose message contains a card file part
    and extensions=[CardsURI]. When routing is provided, a DataPart with
    MessageActionPayload is also attached so the distribution delivers the
    card to the correct channel.

    Args:
        writer: LangGraph StreamWriter from node signature
        card: Card instance (jsx or url)
        routing: Optional delivery routing target (MessageActionPayload)
        metadata: Optional user-defined metadata forwarded to A2A Message.metadata.
                  Keys must not start with "aion:" (reserved for service use).

    Example:
        from aion.core.agent.invocation.card import Card

        def my_node(state: dict, writer: StreamWriter):
            emit_card(writer, Card(jsx="<Card><Text>Hello</Text></Card>"))
            emit_card(writer, Card(jsx="<Card/>"), metadata={"template": "summary"})
    """
    writer(CardCustomEvent(card=card, routing=routing, metadata=metadata))


def emit_message(
    writer: StreamWriter,
    message: AIMessage | AIMessageChunk,
    ephemeral: bool = False,
    routing: MessageActionPayload | None = None,
    metadata: dict | None = None,
) -> None:
    """Emit a message event during graph execution.

    Supports both full messages and streaming chunks:
    - AIMessage > TaskStatusUpdateEvent(working, message=...)
    - AIMessageChunk > TaskArtifactUpdateEvent(STREAM_DELTA) for real-time streaming

    When ephemeral=True, both AIMessage and AIMessageChunk produce a
    TaskArtifactUpdateEvent(EPHEMERAL_MESSAGE) that is sent to the client
    but filtered out by the task store (not persisted in task history).

    Args:
        writer: LangGraph StreamWriter from node signature
        message: LangChain message to emit (AIMessage or AIMessageChunk)
        ephemeral: If True, message is not persisted in task history

    Example:
        def my_node(state: dict, writer: StreamWriter):
            # Ephemeral progress notification (not saved)
            emit_message(writer, AIMessage(content="Searching..."), ephemeral=True)

            # Full message (saved in history)
            emit_message(writer, AIMessage(content="Done"))
    """
    writer(MessageCustomEvent(message=message, ephemeral=ephemeral, routing=routing, metadata=metadata))


def emit_task_update(
    writer: StreamWriter,
    message: Optional[AIMessage] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    """Emit a combined task update with message and/or metadata in a single event.

    Produces exactly one TaskStatusUpdateEvent(working, message=..., metadata=...).
    Use this when you need to emit both a message and metadata simultaneously.
    For streaming chunks, use emit_message() with AIMessageChunk instead.

    Args:
        writer: LangGraph StreamWriter from node signature
        message: Full message to emit (AIMessage only, not chunks)
        metadata: Metadata dict to merge into the task

    Raises:
        ValueError: If neither message nor metadata is provided

    Example:
        def my_node(state: dict, writer: StreamWriter):
            # Message + metadata together as one event
            emit_task_update(
                writer,
                message=AIMessage(content="Analysis complete"),
                metadata={"progress": 100, "step": "done"},
            )

            # Metadata only
            emit_task_update(writer, metadata={"progress": 50})

            # Message only (equivalent to emit_message with AIMessage)
            emit_task_update(writer, message=AIMessage(content="Done"))
    """
    if message is None and metadata is None:
        raise ValueError("At least one of 'message' or 'metadata' must be provided")

    writer(TaskUpdateCustomEvent(message=message, metadata=metadata))


def emit_reaction(
    writer: StreamWriter,
    payload: ReactionActionPayload,
) -> None:
    """Emit a reaction action event during graph execution.

    Instructs the distribution to add or remove a reaction on an existing provider message.
    Produces an outbound A2A message with a single ReactionActionPayload DataPart.

    Args:
        writer: LangGraph StreamWriter from node signature
        payload: Reaction action to perform

    Example:
        def my_node(state: dict, writer: StreamWriter):
            emit_reaction(writer, ReactionActionPayload(
                context_id="C06ROOM123",
                message_id="1728162300.551219",
                reaction_key="thumbsup",
                operation="add",
            ))
    """
    writer(ReactionCustomEvent(payload=payload))

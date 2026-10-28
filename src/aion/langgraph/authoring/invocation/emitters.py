"""Helpers for emitting events from LangGraph nodes during invocation.

This module provides helper functions to emit custom events during graph execution.
All events are emitted via LangGraph's custom stream mode.

Architecture (see thread_message_concept.md):
  Builders (aion.core)  →  emit_* (here, explicit params)  →  Thread (magic wrapper)
"""

from a2a.types import Artifact
from aion.core.a2a.extensions.messaging import MessageActionPayload, ReactionActionPayload
from aion.core.agent.invocation.card import Card
from langchain_core.messages import AIMessage, AIMessageChunk
from langgraph.types import StreamWriter
from typing import Any, Optional

from ..events.custom_events import (
    ArtifactCustomEvent,
    CardCustomEvent,
    MessageCustomEvent,
    ReactionCustomEvent,
    ThinkingCustomEvent,
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
        routing: Optional delivery routing target (MessageActionPayload), added
                 to the artifact as a data part beside its content. With
                 ``append``, give it on the chunk that starts the artifact only:
                 every chunk's parts stay in the artifact.
        append: If True, append to previously sent artifact
        is_last_chunk: If True, this is the final chunk
        metadata: Optional user-defined metadata merged into A2A Artifact.metadata.
                  Keys must not start with "aion:" (reserved for service use).

    Example:
        from aion.core.a2a import data_artifact, url_artifact

        def my_node(state: dict, writer: StreamWriter):
            emit_artifact(writer, url_artifact("https://example.com/r.pdf", mime_type="application/pdf"))
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
        routing: MessageActionPayload | None = None,
        metadata: dict | None = None,
) -> None:
    """Emit a message event during graph execution.

    Supports both full messages and streaming chunks:
    - AIMessage > TaskStatusUpdateEvent(working, message=...), kept in the task's history
    - AIMessageChunk > TaskArtifactUpdateEvent(STREAM_DELTA), a chunk of the streamed answer;
      a full AIMessage that follows the chunks ends their section with its text instead of
      becoming a message

    Args:
        writer: LangGraph StreamWriter from node signature
        message: LangChain message to emit (AIMessage or AIMessageChunk)
        routing: Optional delivery routing target (MessageActionPayload)
        metadata: Optional user-defined metadata forwarded to A2A Message.metadata.
                  Keys must not start with "aion:" (reserved for service use).

    Example:
        def my_node(state: dict, writer: StreamWriter):
            emit_message(writer, AIMessage(content="Done"))
    """
    writer(MessageCustomEvent(message=message, routing=routing, metadata=metadata))


def emit_thinking(
        writer: StreamWriter,
        text: str,
        *,
        metadata: dict | None = None,
) -> None:
    """Emit one thought of the agent during graph execution.

    The thought becomes one more text part of the task's
    ``aion:thinking-delta`` artifact, which keeps every thought of the task
    for a client to show how the answer came about. It is not the answer and
    not a message, and it does not enter the graph's state.

    Args:
        writer: LangGraph StreamWriter from node signature
        text: The thought
        metadata: Optional user-defined metadata merged into A2A Artifact.metadata.
                  Keys must not start with "aion:" (reserved for service use).

    Example:
        def my_node(state: dict, writer: StreamWriter):
            emit_thinking(writer, "Looking up the account...")
    """
    writer(ThinkingCustomEvent(text=text, metadata=metadata))


def emit_reaction(
        writer: StreamWriter,
        payload: ReactionActionPayload,
) -> None:
    """Emit a reaction action event during graph execution.

    Instructs the distribution to add or remove a reaction on an existing provider message.
    Produces the ``aion:reaction`` artifact with a single ReactionActionPayload DataPart,
    which the task keeps; a reaction is not a result of the task.

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

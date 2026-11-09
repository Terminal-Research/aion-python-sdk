"""Streaming helpers for emitting events from ADK agents.

This module provides helper functions to emit events during ADK agent execution.
All events are emitted via the ContextVar-based ADK event emitter set up by ADKStreamExecutor.

Architecture (see thread_message_concept.md):
  Builders (aion.core)  →  emit_* (here, explicit params)  →  Thread (magic wrapper)

All emit_* functions follow the same pattern:
  - actual data goes in event.content (native ADK)
  - aion:output carries only routing metadata (what to wrap into)
"""

from __future__ import annotations
import logging

from a2a.types import Artifact
from aion.adk.authoring.constants import AION_OUTPUT_KEY, AION_ROUTING_KEY
from aion.core.a2a.extensions.messaging import MessageActionPayload, ReactionActionPayload
from aion.core.agent.invocation.card import Card
from google.adk.events import Event
from google.genai import types
from google.protobuf.json_format import MessageToDict
from .context_vars import EventEmitter
from .event_metadata import AionOutput, CardOutput, PublishedArtifact

from .invocation_context import AionInvocationContext

logger = logging.getLogger(__name__)


def emit_message(
        emitter: EventEmitter,
        text: str,
        *,
        routing: MessageActionPayload | None = None,
        metadata: dict | None = None,
) -> None:
    """Emit a text message during ADK agent execution.

    The message reaches the client as it is sent and stays in the task's
    history.

    Args:
        emitter: ADK event emitter callable from the invocation ContextVar
        text: Text content to emit
        routing: Optional delivery routing target (MessageActionPayload)
        metadata: Optional user-defined metadata forwarded to A2A Message.metadata.
                  Keys must not start with "aion:" (reserved for service use).

    Example:
        def my_agent(ctx, emitter):
            emit_message(emitter, "Processing your request...")
            emit_message(emitter, "Done", metadata={"trace_id": "abc"})
    """
    meta: dict = {}
    if metadata:
        meta.update(metadata)
    if routing is not None:
        meta[AION_ROUTING_KEY] = routing.model_dump(by_alias=True, exclude_none=True)

    emitter(Event(
        author="agent",
        content=types.Content(parts=[types.Part(text=text)], role="model"),
        partial=False,
        custom_metadata=meta or None,
    ))


def emit_thinking(
        emitter: EventEmitter,
        text: str,
        *,
        metadata: dict | None = None,
) -> None:
    """Emit one thought of the agent during ADK agent execution.

    The thought becomes one more text part of the task's
    ``aion:thinking-delta`` artifact, which keeps every thought of the task
    for a client to show how the answer came about. It is not the answer and
    not a message, and it stays out of the ADK session: the model does not
    read it back as something it said.

    Args:
        emitter: ADK event emitter callable from the invocation ContextVar
        text: The thought
        metadata: Optional user-defined metadata merged into A2A Artifact.metadata.
                  Keys must not start with "aion:" (reserved for service use).

    Example:
        def my_agent(ctx, emitter):
            emit_thinking(emitter, "Looking up the account...")
    """
    meta = dict(metadata) if metadata else {}
    meta[AION_OUTPUT_KEY] = AionOutput(thinking=True).model_dump(exclude_none=True)
    emitter(Event(
        author="agent",
        content=types.Content(parts=[types.Part(text=text)], role="model"),
        partial=False,
        custom_metadata=meta,
    ))


def emit_card(
        emitter: EventEmitter,
        card: Card,
        *,
        routing: MessageActionPayload | None = None,
        metadata: dict | None = None,
) -> None:
    """Emit a card message during ADK agent execution.

    Produces a TaskStatusUpdateEvent whose message contains a card file part
    and extensions=[CardsURI]. When routing is provided, a DataPart with
    MessageActionPayload is also attached.

    Args:
        emitter: ADK event emitter callable from the invocation ContextVar
        card: Card instance (jsx or url)
        routing: Optional delivery routing target (MessageActionPayload)
        metadata: Optional user-defined metadata forwarded to A2A Message.metadata.
                  Keys must not start with "aion:" (reserved for service use).

    Example:
        from aion.core.agent.invocation.card import Card

        def my_agent(ctx, emitter):
            emit_card(emitter, Card(jsx="<Card><Text>Hello</Text></Card>"))
            emit_card(emitter, Card(jsx="<Card/>"), metadata={"template": "summary"})
    """
    meta = dict(metadata) if metadata else {}

    if card.url:
        meta[AION_OUTPUT_KEY] = AionOutput(card=CardOutput(url=card.url)).model_dump(exclude_none=True)
        content = types.Content(parts=[], role="model")
    else:
        meta[AION_OUTPUT_KEY] = AionOutput(card=CardOutput()).model_dump(exclude_none=True)
        content = types.Content(parts=[types.Part(text=card.jsx)], role="model")

    if routing is not None:
        meta[AION_ROUTING_KEY] = routing.model_dump(by_alias=True, exclude_none=True)

    emitter(Event(
        author="agent",
        content=content,
        partial=False,
        custom_metadata=meta,
    ))


async def emit_artifact(
        emitter: EventEmitter,
        ctx: AionInvocationContext,
        artifact: Artifact,
        *,
        routing: MessageActionPayload | None = None,
        append: bool = False,
        is_last_chunk: bool = True,
        metadata: dict | None = None,
) -> None:
    """Emit a pre-built artifact during ADK agent execution.

    The artifact reaches the client as it is, as ``emit_artifact`` does in
    the LangGraph adapter: every part, its own metadata, and an artifact
    built in chunks with ``append``. It does not go through ADK's artifact
    service, which keeps one part per version; a tool that saves an artifact
    the ADK way (``tool_context.save_artifact``) still does, and both reach
    the client. The server's artifact service reads the task's artifacts by
    name, so ADK's own ``load_artifacts`` finds this one too - as its first
    part, the one ``Part`` an ADK artifact holds.

    Args:
        emitter: ADK event emitter callable from the invocation ContextVar
        ctx: ADK InvocationContext from the invocation ContextVar
        artifact: Pre-built a2a.types.Artifact to emit
        routing: Optional delivery routing target (MessageActionPayload); the
                 converter adds it to the A2A artifact as a data part beside
                 its content. With ``append``, give it on the chunk that
                 starts the artifact only.
        append: If True, append to the artifact sent before under its id
        is_last_chunk: If True, this is the artifact's final chunk
        metadata: Optional user-defined metadata for the artifact's update
                  event, beside the artifact's own. Keys must not start with
                  "aion:" (reserved for service use).

    Example:
        from aion.core.a2a import data_artifact, url_artifact

        async def my_agent(ctx, emitter):
            await emit_artifact(emitter, ctx, url_artifact("https://example.com/r.pdf", mime_type="application/pdf"))
            await emit_artifact(emitter, ctx, data_artifact({"score": 42}, name="result"), metadata={"owner": "agent-x"})
    """
    meta = dict(metadata) if metadata else {}
    meta[AION_OUTPUT_KEY] = AionOutput(
        published=PublishedArtifact(
            artifact=MessageToDict(artifact, preserving_proto_field_name=True),
            append=append,
            last_chunk=is_last_chunk,
        )
    ).model_dump(exclude_none=True)
    if routing is not None:
        meta[AION_ROUTING_KEY] = routing.model_dump(by_alias=True, exclude_none=True)

    emitter(Event(author="agent", content=None, partial=False, custom_metadata=meta))


def emit_reaction(
        emitter: EventEmitter,
        payload: ReactionActionPayload,
) -> None:
    """Emit a reaction action event during ADK agent execution.

    Instructs the distribution to add or remove a reaction on an existing provider message.
    Produces a TaskArtifactUpdateEvent with artifact_id ``aion:reaction``, which the task
    keeps; a reaction is not a result of the task.

    Args:
        emitter: ADK event emitter callable from the invocation ContextVar
        payload: Reaction action to perform

    Example:
        from aion.core.a2a.extensions.messaging import ReactionActionPayload

        def my_agent(ctx, emitter):
            emit_reaction(emitter, ReactionActionPayload(
                context_id="C06ROOM123",
                message_id="1728162300.551219",
                reaction_key="thumbsup",
                operation="add",
            ))
    """
    emitter(Event(
        author="agent",
        content=None,
        partial=False,
        custom_metadata={
            AION_OUTPUT_KEY: AionOutput(reaction=payload).model_dump(exclude_none=True)
        },
    ))

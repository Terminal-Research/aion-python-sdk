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
from collections.abc import AsyncIterable, Iterable, Mapping

from a2a.types import Artifact
from aion.adk.authoring.constants import AION_OUTPUT_KEY, AION_ROUTING_KEY
from aion.core.a2a.extensions.messaging import MessageActionPayload, ReactionActionPayload
from aion.core.agent.invocation.card import Card
from google.adk.events import Event
from pydantic import BaseModel
from google.genai import types
from google.protobuf.json_format import MessageToDict
from .context_vars import EventEmitter
from .event_metadata import AionOutput, CardOutput, PublishedArtifact

logger = logging.getLogger(__name__)


def _agent_metadata(metadata: dict | None, routing: MessageActionPayload | None) -> dict | None:
    meta = dict(metadata) if metadata else {}
    if routing is not None:
        meta[AION_ROUTING_KEY] = routing.model_dump(by_alias=True, exclude_none=True)
    return meta or None


def _text_event(text: str, *, partial: bool, metadata: dict | None) -> Event:
    return Event(
        author="agent",
        content=types.Content(parts=[types.Part(text=text)], role="model") if text else None,
        partial=partial,
        custom_metadata=metadata,
    )


class _StreamedSection:
    """One section of the streamed answer, sent from the text an iterator yields.

    Every piece of text goes out as a partial ADK Event, a chunk of
    ``aion:stream-delta`` with the routing target and the metadata;
    ``close`` sends the whole text as a complete Event, which ends the
    section, as ADK's own streaming does, and its text is not sent again.
    """

    def __init__(self, emitter: EventEmitter, routing: MessageActionPayload | None, metadata: dict | None):
        self._emitter = emitter
        self._metadata = _agent_metadata(metadata, routing)
        self._text = ""

    def send(self, item: object) -> None:
        if not isinstance(item, str):
            logger.warning("A streamed iterator yielded unsupported type: %s. Expected str.", type(item).__name__)
            return
        self._emitter(_text_event(item, partial=True, metadata=self._metadata))
        self._text += item

    def close(self) -> Event | None:
        if not self._text:
            return None
        event = _text_event(self._text, partial=False, metadata=self._metadata)
        self._emitter(event)
        return event


def emit_message(
        emitter: EventEmitter,
        message: str | Iterable[str],
        *,
        routing: MessageActionPayload | None = None,
        metadata: dict | None = None,
) -> Event | None:
    """Emit a text message, or stream the text an iterator yields, during ADK agent execution.

    - str > a message: it reaches the client as it is sent, and the task's
      history and the ADK session keep it as what the agent said
    - an iterator of str > one section of the streamed answer: every piece
      of text is a partial ADK Event, a chunk of ``aion:stream-delta``, and
      the whole text follows as a complete Event that ends the section; its
      text is not sent again, and the ADK session keeps it, not the chunks.
      An iterator that raises ends the section with what it yielded, and
      the error is logged; an item that is not text is left out with a
      warning.

    An ADK ``Event`` is not a message here: an agent yields its own events,
    and ``Thread.post`` passes one on as it is.

    Reading an iterator blocks the code that reads it; an async iterator
    streams with ``await aemit_message(...)``.

    Args:
        emitter: ADK event emitter callable from the invocation ContextVar
        message: Text to send, or an iterator of text to stream
        routing: Optional delivery routing target (MessageActionPayload);
                 a streamed section takes it on its first chunk
        metadata: Optional user-defined metadata forwarded to A2A Message.metadata.
                  Keys must not start with "aion:" (reserved for service use).

    Returns:
        The Event emitted; for an iterator, the complete Event that ended its
        section, or None when it yielded no text.

    Raises:
        TypeError: ``message`` is an async iterator, which ``aemit_message``
            streams, or neither text nor an iterator of text.

    Example:
        def my_agent(ctx, emitter):
            emit_message(emitter, "Processing your request...")
            emit_message(emitter, iter(["It ", "is ", "sunny."]), metadata={"trace_id": "abc"})
    """
    if isinstance(message, str):
        event = _text_event(message, partial=False, metadata=_agent_metadata(metadata, routing))
        emitter(event)
        return event
    if hasattr(message, "__aiter__"):
        raise TypeError("emit_message() cannot read an async iterator; stream it with `await aemit_message(...)`.")
    # A pydantic model - an ADK Event, a genai Content - iterates over its fields, not text.
    if isinstance(message, (bytes, Mapping, BaseModel)) or not isinstance(message, Iterable):
        raise TypeError(f"emit_message() takes text or an iterator of text, not {type(message).__name__}.")
    section = _StreamedSection(emitter, routing, metadata)
    try:
        for item in message:
            section.send(item)
    except Exception as e:
        logger.error("Error reading the iterator emit_message() streams: %s", e, exc_info=True)
    return section.close()


async def aemit_message(
        emitter: EventEmitter,
        message: str | Iterable[str] | AsyncIterable[str],
        *,
        routing: MessageActionPayload | None = None,
        metadata: dict | None = None,
) -> Event | None:
    """Emit what ``emit_message`` does, and stream an async iterator of text as well.

    An async iterator streams as one section of the answer, ended by the
    complete Event, which is returned, as ``emit_message`` describes for an
    iterator. Anything else is ``emit_message``.

    Args:
        emitter: ADK event emitter callable from the invocation ContextVar
        message: Text, or a sync or async iterator of text
        routing: Optional delivery routing target (MessageActionPayload)
        metadata: Optional user-defined metadata, as for ``emit_message``

    Returns:
        The Event emitted; for an iterator, the complete Event that ended its
        section, or None when it yielded no text.

    Example:
        async def my_agent(ctx, emitter):
            await aemit_message(emitter, summarize(ctx))
    """
    if not hasattr(message, "__aiter__"):
        return emit_message(emitter, message, routing=routing, metadata=metadata)
    section = _StreamedSection(emitter, routing, metadata)
    try:
        async for item in message:
            section.send(item)
    except Exception as e:
        logger.error("Error reading the async iterator aemit_message() streams: %s", e, exc_info=True)
    return section.close()


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


def emit_artifact(
        emitter: EventEmitter,
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

        def my_agent(ctx, emitter):
            emit_artifact(emitter, url_artifact("https://example.com/r.pdf", mime_type="application/pdf"))
            emit_artifact(emitter, data_artifact({"score": 42}, name="result"), metadata={"owner": "agent-x"})
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

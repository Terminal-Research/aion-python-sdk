"""Helpers for emitting events from LangGraph nodes during invocation.

This module provides helper functions to emit custom events during graph execution.
All events are emitted via LangGraph's custom stream mode.

Architecture (see thread_message_concept.md):
  Builders (aion.core)  →  emit_* (here, explicit params)  →  Thread (magic wrapper)
"""

import logging
from collections.abc import AsyncIterable, Iterable, Iterator, Mapping
from contextlib import contextmanager

from a2a.types import Artifact
from aion.core.a2a.extensions.messaging import MessageActionPayload, ReactionActionPayload
from aion.core.agent.invocation.card import Card
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.runnables.config import var_child_runnable_config
from langgraph.constants import TAG_NOSTREAM
from langgraph.types import StreamWriter
from pydantic import BaseModel
from typing import Any, Optional
from uuid import uuid4

from ..events.custom_events import (
    ArtifactCustomEvent,
    CardCustomEvent,
    MessageCustomEvent,
    ReactionCustomEvent,
    ThinkingCustomEvent,
)

logger = logging.getLogger(__name__)


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


class _StreamedSection:
    """One section of the streamed answer, sent from the items of an iterator.

    Every item goes out as a chunk of ``aion:stream-delta`` with the routing
    target and the metadata; ``close`` sends the whole message that ends the
    section, and its text is not sent again. When every chunk carried one id,
    the chunks of one model call, the whole message takes that id: should
    LangGraph stream the call as well, the server knows the message as that
    call's own and does not send its text a second time.
    """

    def __init__(self, writer: StreamWriter, routing: MessageActionPayload | None, metadata: dict | None):
        self._writer = writer
        self._routing = routing
        self._metadata = metadata
        self._text = ""
        self._ids: set[str | None] = set()

    def send(self, item: Any) -> None:
        if isinstance(item, str):
            item = AIMessageChunk(content=item)
        if not isinstance(item, AIMessageChunk):
            logger.warning(
                "A streamed iterator yielded unsupported type: %s. Expected str or AIMessageChunk.",
                type(item).__name__,
            )
            return
        _emit_one(self._writer, item, self._routing, self._metadata)
        self._ids.add(item.id)
        self._text += item.text

    def close(self) -> AIMessage | None:
        if not self._text:
            return None
        shared_id = next(iter(self._ids)) if len(self._ids) == 1 else None
        message = AIMessage(content=self._text, id=shared_id or str(uuid4()))
        _emit_one(self._writer, message, self._routing, self._metadata)
        return message


@contextmanager
def _model_calls_off_the_graph_stream() -> Iterator[None]:
    """Keep the model calls started inside this block off LangGraph's ``messages`` stream.

    A model called without a config of its own takes the node's config from
    ``var_child_runnable_config`` when its call starts, and with it the
    callback that LangGraph builds the ``messages`` stream from. That callback
    leaves out a call tagged ``nostream``. The block adds the tag to the
    config in the context, so the model calls that start inside the block
    reach the client only as what the code around them sends.
    """
    config = var_child_runnable_config.get() or {}
    tags = [*(config.get("tags") or []), TAG_NOSTREAM]
    token = var_child_runnable_config.set({**config, "tags": tags})
    try:
        yield
    finally:
        var_child_runnable_config.reset(token)


def _emit_one(
        writer: StreamWriter,
        message: AIMessage | AIMessageChunk,
        routing: MessageActionPayload | None,
        metadata: dict | None,
) -> None:
    if not isinstance(message, AIMessageChunk) and message.id is None:
        message.id = str(uuid4())
    writer(MessageCustomEvent(message=message, routing=routing, metadata=metadata))


def emit_message(
        writer: StreamWriter,
        message: str | AIMessage | AIMessageChunk | Iterable[str | AIMessageChunk],
        routing: MessageActionPayload | None = None,
        metadata: dict | None = None,
) -> AIMessage | AIMessageChunk | None:
    """Emit a message, a chunk of the streamed answer, or a whole streamed answer.

    - str > the text as an AIMessage, a message as below
    - AIMessage > TaskStatusUpdateEvent(working, message=...), kept in the task's history
    - AIMessageChunk > TaskArtifactUpdateEvent(STREAM_DELTA), a chunk of the streamed answer;
      a full AIMessage that follows the chunks ends their section instead of becoming a
      message, and its text is not sent again
    - an iterator of str or AIMessageChunk, ``llm.stream(...)`` say > one section of the
      streamed answer, ended by the whole message, which is returned

    A full message without an id gets one here, on the message itself. A node
    that also returns the message into the graph's state makes LangGraph send
    it once more; under the same id that copy is recognized as the message
    already sent, and neither it nor its files reach the client twice.

    LangGraph streams every model call a node makes on its own ``messages``
    stream. The model calls that start while an iterator is read here are
    kept off that stream, so what the iterator yields is the answer, sent
    once with ``routing`` and ``metadata``; a call whose result the iterator
    does not yield does not reach the client. A call that started before the
    iterator is read - the iterator was read before it was passed here, or
    code running elsewhere feeds it, another thread or a task started
    earlier - and one given ``tags`` in a config of its own stay on the
    graph's stream. For an iterator of that call's chunks the server sends
    the call from the graph's stream and leaves out what is emitted here,
    ``routing`` and ``metadata`` with it; text taken from such a call carries
    no id to know it by, and the answer reaches the client twice. An
    iterator that raises ends the section with what it yielded, and the
    error is logged.

    Reading an iterator blocks the thread that runs the node; in an async
    node, ``await aemit_message(writer, llm.astream(...))`` streams without
    blocking the event loop.

    Args:
        writer: LangGraph StreamWriter from node signature
        message: Text, a LangChain message to emit (AIMessage or AIMessageChunk),
                 or an iterator of str or AIMessageChunk to stream
        routing: Optional delivery routing target (MessageActionPayload)
        metadata: Optional user-defined metadata forwarded to A2A Message.metadata.
                  Keys must not start with "aion:" (reserved for service use).

    Returns:
        The message or chunk emitted, the AIMessage built for text; for an
        iterator, the whole message that ended its section, or None when it
        yielded no text.

    Raises:
        TypeError: ``message`` is an async iterator, which ``aemit_message``
            streams, or of a type that cannot be emitted.

    Example:
        def my_node(state: dict, writer: StreamWriter):
            emit_message(writer, "Done")

        def answer_node(state: dict, writer: StreamWriter):
            return {"messages": [emit_message(writer, llm.stream(state["messages"]))]}
    """
    if isinstance(message, str):
        message = AIMessage(content=message)
    if isinstance(message, AIMessage):
        _emit_one(writer, message, routing, metadata)
        return message
    if hasattr(message, "__aiter__"):
        raise TypeError("emit_message() cannot read an async iterator; stream it with `await aemit_message(...)`.")
    # A pydantic model - a message of another role, say - iterates over its fields, not chunks.
    if isinstance(message, (bytes, Mapping, BaseModel)) or not isinstance(message, Iterable):
        raise TypeError(
            f"emit_message() takes text, an AIMessage, an AIMessageChunk or an iterator of str or "
            f"AIMessageChunk, not {type(message).__name__}."
        )
    section = _StreamedSection(writer, routing, metadata)
    try:
        with _model_calls_off_the_graph_stream():
            for item in message:
                section.send(item)
    except Exception as e:
        logger.error("Error reading the iterator emit_message() streams: %s", e, exc_info=True)
    return section.close()


async def aemit_message(
        writer: StreamWriter,
        message: str | AIMessage | AIMessageChunk | Iterable[str | AIMessageChunk] | AsyncIterable[str | AIMessageChunk],
        routing: MessageActionPayload | None = None,
        metadata: dict | None = None,
) -> AIMessage | AIMessageChunk | None:
    """Emit what ``emit_message`` does, and stream an async iterator as well.

    An async iterator, ``llm.astream(...)`` say, streams as one section of the
    answer, ended by the whole message, which is returned; the model calls
    that start while it is read stay off LangGraph's ``messages`` stream, as
    ``emit_message`` describes. Anything else is ``emit_message``.

    Args:
        writer: LangGraph StreamWriter from node signature
        message: Text, a LangChain message, or a sync or async iterator of str or AIMessageChunk
        routing: Optional delivery routing target (MessageActionPayload)
        metadata: Optional user-defined metadata, as for ``emit_message``

    Returns:
        The message or chunk emitted; for an iterator, the whole message that
        ended its section, or None when it yielded no text.

    Example:
        async def answer_node(state: dict, writer: StreamWriter):
            return {"messages": [await aemit_message(writer, llm.astream(state["messages"]))]}
    """
    if not hasattr(message, "__aiter__"):
        return emit_message(writer, message, routing=routing, metadata=metadata)
    section = _StreamedSection(writer, routing, metadata)
    try:
        with _model_calls_off_the_graph_stream():
            async for item in message:
                section.send(item)
    except Exception as e:
        logger.error("Error reading the async iterator aemit_message() streams: %s", e, exc_info=True)
    return section.close()


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

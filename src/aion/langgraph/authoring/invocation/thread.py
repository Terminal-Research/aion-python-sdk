"""LangGraph thread abstraction for streaming agent responses.

Provides a context-aware Thread class that emits messages, cards, artifacts,
and the agent's thoughts via LangGraph's streaming mechanism.
"""

from __future__ import annotations
import logging

from a2a.types import Artifact as A2AArtifact
from aion.core.a2a.extensions.messaging import MessageActionPayload
from aion.core.agent import BaseThread
from aion.core.agent.invocation.card import Card
from langchain_core.messages import AIMessage, AIMessageChunk
from langgraph.config import get_stream_writer
from typing import Any, Optional
from uuid import uuid4

from .emitters import (
    aemit_message,
    emit_artifact,
    emit_card,
    emit_message,
    emit_thinking,
)
from .message import Message

logger = logging.getLogger(__name__)

ReplyResult = Optional[AIMessage]
"""Return type of Thread.post(): the emitted message or message chunk, or None if nothing was sent."""



class Thread(BaseThread):
    """LangGraph conversation thread bound to the current invocation."""

    _message_class = Message

    @staticmethod
    def get_writer():
        try:
            return get_stream_writer()
        except RuntimeError as e:
            logger.debug(
                "LangGraph stream writer not available (outside invocation context): %s",
                e,
            )
            return None

    @staticmethod
    async def _emit_string_as_text_or_card_document(
            writer,
            content: str,
            metadata: dict | None = None,
            routing: Optional[MessageActionPayload] = None,
    ) -> ReplyResult:
        msg = AIMessage(content=content, id=str(uuid4()))
        emit_message(writer, msg, routing=routing, metadata=metadata)
        return msg

    async def post(
            self,
            content: Any,
            *,
            target: Optional[MessageActionPayload] = None,
            metadata: dict | None = None,
    ) -> ReplyResult:
        """Post a message via LangGraph stream.

        When target is provided, routes to that explicit context.
        When target is None, falls back to local A2A delivery.

        Supports str, AIMessage, AIMessageChunk, or async iterator of those types.
        An async iterator over a model's stream, or over text taken from it, sends
        the answer once (see ``aemit_message``).
        metadata is applied only when the SDK constructs the message (str or async iterator);
        it is ignored for AIMessage and AIMessageChunk which are sent as-is, except that an
        AIMessage without an id gets one (see ``emit_message``).
        """
        writer = self.get_writer()
        if writer is None:
            return None

        if isinstance(content, Card):
            emit_card(writer, content, routing=target, metadata=metadata)
            return None

        if isinstance(content, A2AArtifact):
            emit_artifact(writer, content, routing=target, metadata=metadata)
            return None

        if isinstance(content, str):
            return await self._emit_string_as_text_or_card_document(writer, content, metadata, routing=target)
        elif isinstance(content, AIMessageChunk):
            emit_message(writer, content, routing=target)
            return content
        elif isinstance(content, AIMessage):
            emit_message(writer, content, routing=target)
            return content
        elif hasattr(content, "__aiter__"):
            return await aemit_message(writer, content, routing=target, metadata=metadata)
        else:
            logger.warning(
                "Thread.post() received unsupported content type: %s. "
                "Supported types: str, AIMessage, AIMessageChunk, async iterator.",
                type(content).__name__,
            )
            return None

    async def thinking(self, content: str, *, metadata: dict | None = None) -> None:
        """Add one thought to the task's ``aion:thinking-delta`` artifact.

        See ``emit_thinking``: the thought is not a message and does not
        enter the graph's state.
        """
        if not isinstance(content, str):
            logger.warning(
                "Thread.thinking() received unsupported content type: %s. "
                "Supported type: str.",
                type(content).__name__,
            )
            return

        writer = self.get_writer()
        if writer is None:
            return

        emit_thinking(writer, content, metadata=metadata)

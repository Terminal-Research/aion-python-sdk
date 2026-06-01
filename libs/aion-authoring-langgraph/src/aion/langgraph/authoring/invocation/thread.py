from __future__ import annotations

from aion.core.agent import BaseThread
from aion.core.logging import get_logger
from aion.core.types.a2a.extensions.messaging import MessageActionPayload
from langchain_core.messages import AIMessage, AIMessageChunk
from langgraph.config import get_stream_writer
from typing import Any, Optional, Union
from uuid import uuid4

from aion.langgraph.authoring.invocation.message import Message
from aion.langgraph.authoring.stream import emit_message

logger = get_logger()

ReplyResult = Optional[AIMessage]
"""Return type of Thread.post(): the object that was sent, or None if nothing was sent."""


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
        msg = AIMessage(content=content, id=str(uuid4()), metadata=metadata)
        emit_message(writer, msg, routing=routing)
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
        metadata is applied only when the SDK constructs the message (str or async iterator);
        it is ignored for AIMessage and AIMessageChunk which are sent as-is.
        """
        writer = self.get_writer()
        if writer is None:
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
            return await self._stream_from_async_iterator(writer, content, metadata, routing=target)
        else:
            logger.warning(
                "Thread.post() received unsupported content type: %s. "
                "Supported types: str, AIMessage, AIMessageChunk, async iterator.",
                type(content).__name__,
            )
            return None

    @staticmethod
    async def _stream_from_async_iterator(
            writer,
            iterator: Any,
            metadata: dict | None = None,
            routing: Optional[MessageActionPayload] = None,
    ) -> Optional[AIMessage]:
        accumulated = ""
        try:
            async for chunk in iterator:
                if isinstance(chunk, str):
                    emit_message(writer, AIMessageChunk(content=chunk))
                    accumulated += chunk
                elif isinstance(chunk, AIMessageChunk):
                    emit_message(writer, chunk)
                    if chunk.content:
                        accumulated += chunk.content
                else:
                    logger.warning(
                        "Async iterator yielded unsupported type: %s. "
                        "Expected str or AIMessageChunk.",
                        type(chunk).__name__,
                    )
        except Exception as e:
            logger.error(
                "Error processing async iterator in Thread.reply(): %s",
                e,
                exc_info=True,
            )

        if accumulated:
            msg = AIMessage(content=accumulated, id=str(uuid4()), metadata=metadata)
            emit_message(writer, msg, routing=routing)
            return msg

        return None

    async def typing(self, content: str) -> None:
        """Emit a stream-only ephemeral typing/progress indicator.

        For full control over message parameters, use post() with an AIMessage instead.
        """
        if not isinstance(content, str):
            logger.warning(
                "Thread.typing() received unsupported content type: %s. "
                "Supported type: str.",
                type(content).__name__,
            )
            return

        writer = self.get_writer()
        if writer is None:
            return

        msg = AIMessage(content=content)
        emit_message(writer, msg, ephemeral=True)

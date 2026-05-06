from __future__ import annotations

from a2a.types import Artifact, Part
from aion.shared.constants import (
    CARDS_EXTENSION_URI_V1,
    CARDS_MEDIA_TYPE,
    CARDS_PAYLOAD_SCHEMA_V1,
)
from aion.shared.logging import get_logger
from aion.shared.runtime import AionRuntimeContext
from langchain_core.messages import AIMessage, AIMessageChunk
from langgraph.config import get_stream_writer
from typing import TYPE_CHECKING, Any, List, Optional, Union
from uuid import uuid4

from aion.langgraph.events.custom_events import ArtifactCustomEvent
from aion.langgraph.stream import emit_message

if TYPE_CHECKING:
    from .message import Message

logger = get_logger()

ReplyResult = Optional[Union[AIMessage, Artifact]]
"""Return type of Thread.reply(): the object that was sent, or None if nothing was sent."""


class Thread:
    """LangGraph conversation thread bound to the current invocation."""

    def __init__(
            self,
            context: AionRuntimeContext,
            id: Optional[str],
            parent_id: Optional[str],
            network: str,
            default_reply_target: Optional[str],
            writer=None,
    ) -> None:
        self._context = context
        self._writer = writer
        self.id = id
        self.parent_id = parent_id
        self.network = network
        self.default_reply_target = default_reply_target
        self.message = self._build_message()

    @classmethod
    def from_context(cls, context: AionRuntimeContext, writer=None) -> "Thread":
        """Create a Thread from an AionRuntimeContext, extracting thread metadata."""
        payload = context.event.payload if context.event else None

        context_id = getattr(payload, "context_id", None)
        if context_id is None:
            if context.inbox.message is not None and context.inbox.message.context_id:
                context_id = context.inbox.message.context_id
            elif context.inbox.task is not None and context.inbox.task.context_id:
                context_id = context.inbox.task.context_id

        trajectory = getattr(payload, "trajectory", None)
        parent_context_id = getattr(payload, "parent_context_id", None)
        default_reply_target = (
            parent_context_id
            if trajectory == "reply" and parent_context_id
            else context_id
        )

        network = context.identity.network_type if context.identity else "A2A"

        return cls(
            context=context,
            id=context_id,
            parent_id=parent_context_id,
            network=network,
            default_reply_target=default_reply_target,
            writer=writer,
        )

    def _build_message(self):
        if self._context.inbox.message is None:
            return None

        from .message import Message
        return Message(context=self._context, thread=self)

    def _get_writer(self):
        if self._writer is not None:
            return self._writer
        try:
            return get_stream_writer()
        except RuntimeError as e:
            logger.debug(
                "LangGraph stream writer not available (outside invocation context): %s",
                e,
            )
            return None

    @staticmethod
    def _detect_jsx_card_markup(text: str) -> bool:
        return text.startswith("<Card")

    async def _emit_string_as_text_or_card_document(
            self,
            writer,
            content: str,
            metadata: dict | None = None,
    ) -> ReplyResult:
        if self._detect_jsx_card_markup(content):
            return await self._emit_jsx_card_as_artifact(writer, content, metadata)
        else:
            msg = AIMessage(content=content, id=str(uuid4()), metadata=metadata)
            emit_message(writer, msg)
            return msg

    async def reply(self, content: Any, *, metadata: dict | None = None) -> ReplyResult:
        """Add a durable reply to the current thread via LangGraph stream.

        Supports str, AIMessage, AIMessageChunk, or async iterator of those types.
        """
        writer = self._get_writer()
        if writer is None:
            return None

        if isinstance(content, str):
            return await self._emit_string_as_text_or_card_document(writer, content, metadata)
        elif isinstance(content, AIMessage):
            emit_message(writer, content)
            return content
        elif isinstance(content, AIMessageChunk):
            emit_message(writer, content)
            return content
        elif hasattr(content, "__aiter__"):
            return await self._stream_from_async_iterator(writer, content, metadata)
        else:
            logger.warning(
                "Thread.reply() received unsupported content type: %s. "
                "Supported types: str, AIMessage, AIMessageChunk, async iterator.",
                type(content).__name__,
            )
            return None

    @staticmethod
    async def _stream_from_async_iterator(
            writer,
            iterator: Any,
            metadata: dict | None = None,
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
            emit_message(writer, msg)
            return msg

        return None

    @staticmethod
    async def _emit_jsx_card_as_artifact(
            writer,
            card_jsx: str,
            metadata: dict | None = None,
    ) -> Artifact:
        card_metadata = {CARDS_EXTENSION_URI_V1: {"schema": CARDS_PAYLOAD_SCHEMA_V1}}

        if metadata:
            card_metadata.update(metadata)

        card_part = Part(
            raw=card_jsx.encode("utf-8"),
            media_type=CARDS_MEDIA_TYPE,
            metadata=card_metadata,
        )

        card_artifact = Artifact(
            artifact_id=str(uuid4()),
            name="card",
            parts=[card_part],
        )

        writer(ArtifactCustomEvent(artifact=card_artifact, is_last_chunk=True))
        return card_artifact

    @staticmethod
    async def post(content, *, target=None, metadata=None) -> None:
        """Explicit outbound post distinct from the default reply target."""
        logger.warning("Thread.post() is not yet implemented.")

    @staticmethod
    async def history(limit: int = 20, before=None) -> List:
        """Request recent conversation history through the control plane."""
        logger.warning(
            "Thread.history() is not yet implemented. "
            "Returning empty list. "
            "TODO: implement via aion-api-client control plane API."
        )
        return []

    async def typing(
            self,
            content: Union[str, AIMessage, AIMessageChunk],
    ) -> None:
        """Emit a stream-only ephemeral typing/progress indicator."""
        writer = self._get_writer()
        if writer is None:
            return

        if isinstance(content, str):
            msg = AIMessage(content=content)
        elif isinstance(content, (AIMessage, AIMessageChunk)):
            msg = content
        else:
            logger.warning(
                "Thread.typing() received unsupported content type: %s. "
                "Supported types: str, AIMessage, AIMessageChunk.",
                type(content).__name__,
            )
            return

        emit_message(writer, msg, ephemeral=True)

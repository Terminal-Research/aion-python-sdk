from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Union
from uuid import uuid4

from a2a.types import Artifact, Part
from aion.shared.constants import (
    CARDS_EXTENSION_URI_V1,
    CARDS_PAYLOAD_SCHEMA_V1,
    CARDS_MEDIA_TYPE,
)
from aion.shared.logging import get_logger
from langchain_core.messages import AIMessage, AIMessageChunk
from langgraph.config import get_stream_writer

from ..events.custom_events import ArtifactCustomEvent
from ..stream import emit_message

logger = get_logger()

ReplyResult = Optional[Union[AIMessage, Artifact]]
"""Return type of Thread.reply(): the object that was sent, or None if nothing was sent.

- AIMessage / AIMessageChunk (subclass) — a text message or streaming chunk
- Artifact — a card or other artifact
- None — writer was unavailable; nothing was sent
"""


@dataclass
class Thread:
    """LangGraph conversation thread bound to the current invocation."""

    id: str
    """Unique identifier for this conversation thread (context_id from event payload)."""
    parent_id: Optional[str]
    """Parent thread identifier for thread continuity, None if this is a root thread."""
    network: str
    """Type of network/provider this thread exists on (e.g., 'slack', 'teams')."""
    default_reply_target: Optional[str]
    """Default target for replies (trajectory from event payload), routing agent responses appropriately."""

    @staticmethod
    def _get_writer():
        """Get LangGraph stream writer, handling errors gracefully.

        Returns:
            Stream writer if available, None otherwise.
        """
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
        """Detect if text contains JSX card markup (starts with <Card tag).

        Args:
            text: Text content to check

        Returns:
            True if text is a JSX card document, False for plain text
        """
        return text.startswith("<Card")

    async def _emit_string_as_text_or_card_document(
            self,
            writer,
            content: str,
            metadata: dict | None = None,
    ) -> ReplyResult:
        """Emit string content as either plain text or a JSX card document.

        If the string starts with <Card markup, it's treated as a JSX card document.
        Otherwise, it's emitted as plain text message.

        Args:
            writer: LangGraph StreamWriter
            content: String content to emit
            metadata: Optional metadata to attach to the message

        Returns:
            The emitted AIMessage for plain text, or the Artifact for JSX card documents.
        """
        if self._detect_jsx_card_markup(content):
            return await self._emit_jsx_card_as_artifact(writer, content, metadata)
        else:
            msg = AIMessage(content=content, id=str(uuid4()), metadata=metadata)
            emit_message(writer, msg)
            return msg

    async def reply(self, content: Any, *, metadata: dict | None = None) -> ReplyResult:
        """Add a durable reply to the current thread via LangGraph stream.

        Supports multiple content types:
        - str: plain text or JSX-like card document
        - AIMessage: full message with optional metadata
        - AIMessageChunk: single streaming chunk
        - async iterator: yields str or AIMessageChunk objects;
          chunks are streamed in real-time and accumulated into a final durable AIMessage
        - TODO: message builder for low-level message construction

        Args:
            content: Message content in one of the supported formats
            metadata: Optional metadata dict to attach to the final message

        Returns:
            The object that was sent:
            - AIMessage for str, AIMessage, or accumulated async iterator
            - AIMessageChunk for a single chunk
            - Artifact for JSX card documents
            - None if the writer was unavailable (nothing was sent)
        """
        writer = self._get_writer()
        if writer is None:
            return None

        # Plain text or JSX card document
        if isinstance(content, str):
            return await self._emit_string_as_text_or_card_document(writer, content, metadata)

        # Full AIMessage
        elif isinstance(content, AIMessage):
            emit_message(writer, content)
            return content

        # Single AIMessageChunk (streaming chunk, not accumulated)
        elif isinstance(content, AIMessageChunk):
            emit_message(writer, content)
            return content

        # Async iterator: stream chunks + accumulate final message
        elif hasattr(content, '__aiter__'):
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
        """Stream chunks in real-time while accumulating content, then emit final durable message.

        For each chunk yielded by iterator:
        1. If it's a string, wrap in AIMessageChunk and emit for streaming
        2. If it's AIMessageChunk, emit directly for streaming
        3. Accumulate the text content from both

        After iterator completes, emit all accumulated content as a single final AIMessage.

        Args:
            writer: LangGraph StreamWriter
            iterator: Async iterator yielding str or AIMessageChunk objects
            metadata: Optional metadata to attach to the final durable message

        Returns:
            The final accumulated AIMessage, or None if nothing was accumulated.
        """
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
        """Emit JSX card as an A2A artifact with card payload metadata extension.

        JSX card document is encoded as a Part, wrapped in an Artifact, and emitted
        via ArtifactCustomEvent for streaming to the client.

        Args:
            writer: LangGraph StreamWriter
            card_jsx: JSX-like card document string to emit
            metadata: Optional metadata to merge with card payload metadata

        Returns:
            The emitted Artifact.
        """
        card_metadata = {
            CARDS_EXTENSION_URI_V1: {
                "schema": CARDS_PAYLOAD_SCHEMA_V1
            }
        }

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
        """Emit a stream-only ephemeral typing/progress indicator.

        Args:
            content: Message content to emit. Accepts str, AIMessage, or AIMessageChunk.
                     Required — a warning is logged and the call is a no-op if omitted.
        """
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

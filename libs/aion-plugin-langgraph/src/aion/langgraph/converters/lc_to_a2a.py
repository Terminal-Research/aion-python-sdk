"""Converts LangChain content blocks to A2A Part objects."""

from __future__ import annotations

import base64
import logging

from a2a.types import Part
from langchain_core.messages import BaseMessage
from langchain_core.messages.content import ContentBlock  # type: ignore[attr-defined]

logger = logging.getLogger(__name__)

_DATA_BLOCK_TYPES = frozenset({"image", "audio", "video", "file"})
_TOOL_BLOCK_TYPES = frozenset({"tool_call", "server_tool_call", "invalid_tool_call"})


class LcToA2AConverter:
    """Converts LangChain ContentBlock objects to A2A Part objects.

    Tool blocks (tool_call, server_tool_call, invalid_tool_call) are skipped —
    they're not surfaced at the A2A level. Unknown block types fall back to
    DataPart so nothing is silently dropped.

    Not meant to be instantiated — use from_message() or from_block() directly.
    """

    @classmethod
    def from_message(cls, message: BaseMessage, *, include_reasoning: bool = False) -> list[Part]:
        """Convert all content blocks of a message to A2A Parts, skipping tool blocks."""
        parts: list[Part] = []
        for block in message.content_blocks:
            part = cls.from_block(block, include_reasoning=include_reasoning)
            if part is not None:
                parts.append(part)
        return parts

    @classmethod
    def from_block(cls, block: ContentBlock, *, include_reasoning: bool = False) -> Part | None:
        """Convert a single ContentBlock to an A2A Part.

        Returns None for tool-related blocks and for reasoning blocks when
        include_reasoning is False (default).
        """
        block_type: str = block.get("type", "")

        if block_type in _TOOL_BLOCK_TYPES:
            return None

        if block_type == "reasoning":
            return cls._from_reasoning(block) if include_reasoning else None

        if block_type == "text":
            return cls._from_text(block)
        if block_type in _DATA_BLOCK_TYPES:
            return cls._from_data_content(block)

        logger.debug("Unhandled ContentBlock type %r – wrapping as DataPart", block_type)
        return cls._fallback(block)

    @staticmethod
    def _from_text(block: ContentBlock) -> Part:
        return Part(text=block.get("text", ""))

    @staticmethod
    def _from_reasoning(block: ContentBlock) -> Part:
        """Reasoning blocks are surfaced as plain text with metadata marking their origin."""
        return Part(
            text=block.get("reasoning", ""),
            metadata={
                "lc_block_type": "reasoning",
                "lc_block_id": block.get("id", ""),
            },
        )

    @classmethod
    def _from_data_content(cls, block: ContentBlock) -> Part:
        """Handle image / audio / video / file blocks, dispatching by source_type."""
        source_type: str = block.get("source_type", "")
        mime_type: str | None = block.get("mime_type")

        if source_type == "url":
            return Part(url=block.get("url", ""), media_type=mime_type)

        if source_type == "base64":
            raw_bytes = base64.b64decode(block.get("data", ""))
            return Part(raw=raw_bytes, media_type=mime_type or "application/octet-stream")

        if source_type == "id":
            return Part(url=block.get("id", ""), media_type=mime_type)

        if source_type == "plain_text":
            return Part(text=block.get("text", ""))

        logger.warning(
            "Unknown source_type %r in block type %r",
            source_type,
            block.get("type"),
        )
        return cls._fallback(block)

    @staticmethod
    def _fallback(block: ContentBlock) -> Part:
        """Wrap an unknown block verbatim as a data Part."""
        return Part(
            data=dict(block),
            metadata={"lc_block_type": block.get("type", "unknown")},
        )

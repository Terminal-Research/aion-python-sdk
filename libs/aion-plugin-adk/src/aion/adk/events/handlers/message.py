"""Message event handler.

This module handles conversion of ADK message events (streaming and complete)
to unified MessageEvent format.
"""

from typing import Any, Optional

from aion.shared.agent.adapters import ExecutionEvent, MessageEvent
from aion.shared.logging import get_logger

from .base import EventHandler

logger = get_logger()


class MessageEventHandler(EventHandler):
    """Handler for ADK message events.

    This handler processes both streaming chunks and complete messages,
    converting them to unified MessageEvent format.
    """

    def can_handle(self, adk_event: Any) -> bool:
        """Check if event is a message (streaming or complete).

        Args:
            adk_event: ADK event to check

        Returns:
            bool: True if event is a streaming chunk or complete message
        """
        return self._is_streaming_chunk(adk_event) or self._is_complete_message(adk_event)

    def handle(self, adk_event: Any) -> Optional[ExecutionEvent]:
        """Convert ADK message event to MessageEvent.

        Args:
            adk_event: ADK event to convert

        Returns:
            MessageEvent: Unified message event
        """
        if self._is_streaming_chunk(adk_event):
            return self._convert_streaming_chunk(adk_event)

        if self._is_complete_message(adk_event):
            return self._convert_complete_message(adk_event)

        return None

    @staticmethod
    def _is_streaming_chunk(adk_event: Any) -> bool:
        return hasattr(adk_event, "partial") and adk_event.partial

    @staticmethod
    def _is_complete_message(adk_event: Any) -> bool:
        return (
            hasattr(adk_event, "partial")
            and not adk_event.partial
            and hasattr(adk_event, "content")
            and adk_event.content is not None
        )

    def _convert_streaming_chunk(self, adk_event: Any) -> MessageEvent:
        content = str(adk_event.content) if hasattr(adk_event, "content") else ""
        author = adk_event.author if hasattr(adk_event, "author") else "assistant"

        return MessageEvent(
            data=content,
            role=author,
            is_streaming=True,
            metadata=self.extract_metadata(adk_event),
        )

    def _convert_complete_message(self, adk_event: Any) -> MessageEvent:
        content = str(adk_event.content)
        author = adk_event.author if hasattr(adk_event, "author") else "assistant"

        return MessageEvent(
            data=content,
            role=author,
            is_streaming=False,
            metadata=self.extract_metadata(adk_event),
        )


__all__ = ["MessageEventHandler"]

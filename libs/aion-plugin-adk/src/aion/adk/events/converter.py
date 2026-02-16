"""ADK event converter.

This module provides the ADKEventConverter for converting ADK events
to unified ExecutionEvent format.
"""

from typing import Any, Optional

from aion.shared.agent.adapters import (
    ExecutionEvent,
    MessageEvent,
)
from aion.shared.logging import get_logger

from aion.adk.utils.message_parts import extract_message_parts

logger = get_logger()


class ADKEventConverter:
    """Converter for ADK events to unified ExecutionEvent format.

    This converter processes ADK Event objects and extracts:
    - Message content (text, thoughts)
    """

    def convert(
        self,
        adk_event: Any,
        metadata: Optional[Any] = None
    ) -> Optional[ExecutionEvent]:
        """Convert ADK event to unified ExecutionEvent.

        Extracts message content (text, thoughts) from ADK events.

        Args:
            adk_event: ADK event to convert
            metadata: Optional additional metadata (not currently used)

        Returns:
            ExecutionEvent: Unified event, or None if conversion fails
        """
        if adk_event is None:
            return None

        try:
            # Extract metadata once
            event_metadata = self._extract_metadata(adk_event)

            # Extract message content (text, thoughts)
            message_event = self._convert_message(adk_event, event_metadata)
            if message_event:
                return message_event

            logger.debug(f"No content to convert in ADK event: {type(adk_event).__name__}")
            return None

        except Exception as e:
            logger.warning(f"Failed to convert ADK event: {e}")
            return None

    def _extract_metadata(self, adk_event: Any) -> dict[str, Any]:
        """Extract metadata from ADK event.

        Args:
            adk_event: ADK event to extract metadata from

        Returns:
            dict: Metadata dictionary with ADK-specific fields
        """
        metadata = {
            "adk_type": type(adk_event).__name__,
        }

        for field in ["id", "invocation_id", "timestamp", "branch"]:
            if hasattr(adk_event, field):
                value = getattr(adk_event, field)
                if value is not None:
                    metadata[f"adk_{field}"] = value

        return metadata

    def _convert_message(
        self,
        adk_event: Any,
        event_metadata: dict[str, Any]
    ) -> Optional[MessageEvent]:
        """Convert message content to MessageEvent.

        Args:
            adk_event: ADK event
            event_metadata: Extracted metadata

        Returns:
            MessageEvent with content parts, or None
        """
        if not hasattr(adk_event, "content"):
            return None

        # Extract content parts (text, thoughts - skips function calls/responses)
        content_parts = extract_message_parts(adk_event.content)
        if not content_parts:
            return None

        author = getattr(adk_event, "author", "assistant")

        return MessageEvent(
            content=content_parts,
            role=author,
            is_chunk=False,
            is_last_chunk=False,
            metadata=event_metadata,
        )


__all__ = ["ADKEventConverter"]

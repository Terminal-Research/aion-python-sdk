"""Custom/fallback event handler.

This module handles conversion of unrecognized ADK events
to unified CustomEvent format.
"""

from typing import Any, Optional

from aion.shared.agent.adapters import CustomEvent, ExecutionEvent
from aion.shared.logging import get_logger

from .base import EventHandler

logger = get_logger()


class CustomEventHandler(EventHandler):
    """Fallback handler for unrecognized ADK events.

    This handler processes any ADK events that don't fit into other categories,
    converting them to unified CustomEvent format with generic data extraction.
    """

    def can_handle(self, adk_event: Any) -> bool:
        """Check if event can be handled.

        Args:
            adk_event: ADK event to check

        Returns:
            bool: Always returns True as this is a fallback handler
        """
        return True

    def handle(self, adk_event: Any) -> Optional[ExecutionEvent]:
        """Convert unrecognized ADK event to CustomEvent.

        Args:
            adk_event: ADK event to convert

        Returns:
            CustomEvent: Unified custom event with extracted data
        """
        return self._convert_to_custom_event(adk_event)

    def _convert_to_custom_event(self, adk_event: Any) -> CustomEvent:
        event_data = {}
        for attr in ["content", "author", "id", "timestamp"]:
            if hasattr(adk_event, attr):
                event_data[attr] = getattr(adk_event, attr)

        return CustomEvent(
            data=event_data,
            metadata=self.extract_metadata(adk_event),
        )


__all__ = ["CustomEventHandler"]

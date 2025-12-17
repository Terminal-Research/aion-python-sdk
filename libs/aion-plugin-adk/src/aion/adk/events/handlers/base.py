"""Base interface for event handlers.

This module defines the abstract base for different event type handlers.
Each handler is responsible for converting a specific type of ADK event
to the unified ExecutionEvent format.
"""

from abc import ABC, abstractmethod
from typing import Any, Optional

from aion.shared.agent.adapters import ExecutionEvent


class EventHandler(ABC):
    """Abstract base for event handlers.

    Event handlers convert specific types of ADK events into unified ExecutionEvent objects.
    Each handler focuses on one type of event (messages, tool calls, state updates, etc.).
    """

    @abstractmethod
    def can_handle(self, adk_event: Any) -> bool:
        """Check if this handler can process the given ADK event.

        Args:
            adk_event: ADK event to check

        Returns:
            bool: True if this handler can process the event
        """
        pass

    @abstractmethod
    def handle(self, adk_event: Any) -> Optional[ExecutionEvent]:
        """Convert ADK event to unified ExecutionEvent.

        Args:
            adk_event: ADK event to convert

        Returns:
            ExecutionEvent: Unified event, or None if conversion fails
        """
        pass

    @staticmethod
    def extract_metadata(adk_event: Any) -> dict[str, Any]:
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


__all__ = ["EventHandler"]

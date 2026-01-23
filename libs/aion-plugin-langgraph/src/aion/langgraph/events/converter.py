from typing import Any, Optional

from aion.shared.agent.adapters import (
    ExecutionEvent,
)
from aion.shared.logging import get_logger
from .event_convertors import CustomEventConverter, MessageEventConverter

logger = get_logger()

SKIP_EVENTS = ("values", "updates")


class LangGraphEventConverter:
    """Converts LangGraph-specific events to framework-agnostic ExecutionEvents.

    This class handles the transformation of various LangGraph event types
    (messages, values, updates, custom) into standardized ExecutionEvent types
    that can be processed by the framework-agnostic event handling layer.
    """

    @staticmethod
    def convert(
            event_type: str,
            event_data: Any,
            metadata: Optional[Any] = None
    ) -> Optional[ExecutionEvent]:
        """Convert LangGraph event to typed ExecutionEvent.

        Normalizes LangGraph-specific types into framework-agnostic events:
        - messages > MessageEvent (with streaming detection)
        - values > StateUpdateEvent
        - updates > NodeUpdateEvent
        - custom > CustomEvent

        Args:
            event_type: LangGraph event type
            event_data: LangGraph event data
            metadata: Optional event metadata

        Returns:
            Typed ExecutionEvent or None if unknown type
        """
        if event_type == "messages":
            return MessageEventConverter.convert(event_data, metadata)
        elif event_type == "custom":
            return CustomEventConverter.convert(event_data)
        elif event_type in SKIP_EVENTS:
            return None
        else:
            logger.warning(f"Unknown LangGraph event type: {event_type}")
            return None

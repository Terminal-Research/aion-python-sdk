from typing import Any, Optional

from aion.shared.agent.adapters import (
    CustomEvent,
    ExecutionEvent,
    MessageEvent,
    MessagePart,
    MessagePartType,
)
from aion.shared.logging import get_logger
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage

logger = get_logger()

SKIP_EVENTS = ("values", "updates")


class LangGraphEventConverter:
    """Converts LangGraph-specific events to framework-agnostic ExecutionEvents.

    This class handles the transformation of various LangGraph event types
    (messages, values, updates, custom) into standardized ExecutionEvent types
    that can be processed by the framework-agnostic event handling layer.
    """

    def convert(
            self,
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
            return self._convert_message(event_data, metadata)
        elif event_type == "custom":
            return self._convert_custom_event(event_data)
        elif event_type in SKIP_EVENTS:
            return None
        else:
            logger.warning(f"Unknown LangGraph event type: {event_type}")
            return None

    @staticmethod
    def _convert_message(
            langgraph_message: Any,
            metadata: Optional[Any]
    ) -> MessageEvent:
        """Convert LangGraph message to MessageEvent.

        Extracts content, determines role, and detects streaming vs final messages.

        Args:
            langgraph_message: LangGraph message object
            metadata: Optional message metadata

        Returns:
            MessageEvent Pydantic model with normalized content and metadata
        """
        # Extract content
        if hasattr(langgraph_message, "content"):
            content_text = str(langgraph_message.content)
        else:
            content_text = str(langgraph_message)

        # Create MessagePart with TEXT type
        content_parts = [
            MessagePart(
                type=MessagePartType.TEXT,
                content=content_text,
            )
        ]

        # Determine role based on message type
        role = "agent"
        if isinstance(langgraph_message, (AIMessage, AIMessageChunk)):
            role = "assistant"
        elif isinstance(langgraph_message, HumanMessage):
            role = "user"
        elif isinstance(langgraph_message, SystemMessage):
            role = "system"

        # Detect streaming chunks
        is_streaming_chunk = isinstance(langgraph_message, AIMessageChunk)

        event_metadata = {
            "langgraph_type": type(langgraph_message).__name__,
        }
        if metadata is not None:
            event_metadata["langgraph_metadata"] = metadata

        return MessageEvent(
            content=content_parts,
            role=role,
            is_streaming=is_streaming_chunk,
            metadata=event_metadata,
        )

    @staticmethod
    def _convert_custom_event(event_data: Any) -> CustomEvent:
        """Convert LangGraph custom event to CustomEvent.

        Handles special 'custom_event' field transformation.

        Args:
            event_data: Custom event data from LangGraph

        Returns:
            CustomEvent Pydantic model with normalized data
        """
        if isinstance(event_data, dict):
            emit_event = {k: v for k, v in event_data.items() if k != "custom_event"}
            if "custom_event" in event_data:
                emit_event["event"] = event_data["custom_event"]

            return CustomEvent(data=emit_event)

        return CustomEvent(data=event_data)

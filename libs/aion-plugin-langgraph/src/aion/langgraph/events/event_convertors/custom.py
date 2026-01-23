from typing import Any

from aion.shared.agent.adapters import CustomEvent


class CustomEventConverter:
    """Converts LangGraph custom events to framework-agnostic CustomEvents."""

    @staticmethod
    def convert(event_data: Any) -> CustomEvent:
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

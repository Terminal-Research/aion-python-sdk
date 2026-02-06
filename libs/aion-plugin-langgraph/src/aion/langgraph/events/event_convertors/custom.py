from typing import Any, Optional

from aion.shared.agent.adapters import (
    ArtifactEvent,
    ExecutionEvent,
    StateUpdateEvent,
)
from aion.shared.logging import get_logger

from ..custom_events import (
    ArtifactCustomEvent,
    MessageCustomEvent,
    TaskMetadataCustomEvent,
)
from .message import MessageEventConverter

logger = get_logger()


class CustomEventConverter:
    """Converts LangGraph custom events to typed ExecutionEvent.

    Handles Aion custom event instances.
    Unknown formats are ignored with a warning.
    """

    @staticmethod
    def convert(event_data: Any) -> Optional[ExecutionEvent]:
        """Convert LangGraph custom event to ExecutionEvent.

        Args:
            event_data: Custom event instance from LangGraph

        Returns:
            ExecutionEvent subclass or None if not an Aion custom event
        """
        # Handle Aion custom event instances
        if isinstance(event_data, ArtifactCustomEvent):
            return CustomEventConverter._convert_artifact(event_data)

        elif isinstance(event_data, MessageCustomEvent):
            return CustomEventConverter._convert_message(event_data)

        elif isinstance(event_data, TaskMetadataCustomEvent):
            return CustomEventConverter._convert_task_metadata(event_data)

        else:
            logger.warning(f"Ignoring unknown custom event type: {type(event_data)}")
            return None

    @staticmethod
    def _convert_artifact(event: ArtifactCustomEvent) -> ArtifactEvent:
        """Convert artifact custom event to ArtifactEvent."""
        return ArtifactEvent(
            artifact=event.artifact,
            append=event.append,
            last_chunk=event.last_chunk,
        )

    @staticmethod
    def _convert_message(event: MessageCustomEvent) -> ExecutionEvent:
        """Convert message custom event to MessageEvent using MessageEventConverter."""
        # Use MessageEventConverter to parse message content
        return MessageEventConverter.convert(event.message, metadata=None)

    @staticmethod
    def _convert_task_metadata(event: TaskMetadataCustomEvent) -> StateUpdateEvent:
        """Convert task metadata custom event to StateUpdateEvent."""
        return StateUpdateEvent(
            data={"task_metadata": event.metadata}
        )

"""State update event handler.

This module handles conversion of ADK state update events
to unified StateUpdateEvent format.
"""

from typing import Any, Optional

from aion.shared.agent.adapters import ExecutionEvent, StateUpdateEvent
from aion.shared.logging import get_logger

from .base import EventHandler

logger = get_logger()


class StateUpdateEventHandler(EventHandler):
    """Handler for ADK state update events.

    This handler processes events that contain state changes or artifact updates,
    converting them to unified StateUpdateEvent format.
    """

    def can_handle(self, adk_event: Any) -> bool:
        """Check if event contains state updates.

        Args:
            adk_event: ADK event to check

        Returns:
            bool: True if event has state or artifact updates
        """
        return self._has_state_updates(adk_event)

    def handle(self, adk_event: Any) -> Optional[ExecutionEvent]:
        """Convert ADK state update event to StateUpdateEvent.

        Args:
            adk_event: ADK event to convert

        Returns:
            StateUpdateEvent: Unified state update event
        """
        if self._has_state_updates(adk_event):
            return self._convert_state_update(adk_event)

        return None

    @staticmethod
    def _has_state_updates(adk_event: Any) -> bool:
        if not hasattr(adk_event, "actions"):
            return False
        actions = adk_event.actions
        return (
            actions is not None
            and (
                hasattr(actions, "state_delta")
                or hasattr(actions, "artifact_delta")
            )
        )

    def _convert_state_update(self, adk_event: Any) -> StateUpdateEvent:
        state_data = {}

        if hasattr(adk_event, "actions"):
            actions = adk_event.actions
            if hasattr(actions, "state_delta") and actions.state_delta:
                state_data["state_delta"] = actions.state_delta
            if hasattr(actions, "artifact_delta") and actions.artifact_delta:
                state_data["artifact_delta"] = actions.artifact_delta

        return StateUpdateEvent(
            data=state_data,
            metadata=self.extract_metadata(adk_event),
        )


__all__ = ["StateUpdateEventHandler"]

"""Tool call/response event handler.

This module handles conversion of ADK tool call and response events
to unified CustomEvent format.
"""

from typing import Any, Optional

from aion.shared.agent.adapters import CustomEvent, ExecutionEvent
from aion.shared.logging import get_logger

from .base import EventHandler

logger = get_logger()


class ToolEventHandler(EventHandler):
    """Handler for ADK tool call and response events.

    This handler processes function calls and function responses,
    converting them to unified CustomEvent format.
    """

    def can_handle(self, adk_event: Any) -> bool:
        """Check if event contains tool calls or responses.

        Args:
            adk_event: ADK event to check

        Returns:
            bool: True if event has function calls or responses
        """
        return self._has_function_calls(adk_event) or self._has_function_responses(adk_event)

    def handle(self, adk_event: Any) -> Optional[ExecutionEvent]:
        """Convert ADK tool event to CustomEvent.

        Args:
            adk_event: ADK event to convert

        Returns:
            CustomEvent: Unified custom event with tool data
        """
        if self._has_function_calls(adk_event):
            return self._convert_function_calls(adk_event)

        if self._has_function_responses(adk_event):
            return self._convert_function_responses(adk_event)

        return None

    @staticmethod
    def _has_function_calls(adk_event: Any) -> bool:
        if hasattr(adk_event, "get_function_calls"):
            calls = adk_event.get_function_calls()
            return calls is not None and len(calls) > 0
        return False

    @staticmethod
    def _has_function_responses(adk_event: Any) -> bool:
        if hasattr(adk_event, "get_function_responses"):
            responses = adk_event.get_function_responses()
            return responses is not None and len(responses) > 0
        return False

    def _convert_function_calls(self, adk_event: Any) -> CustomEvent:
        function_calls = adk_event.get_function_calls()

        calls_data = []
        for call in function_calls:
            call_dict = {
                "name": getattr(call, "name", None),
                "arguments": getattr(call, "arguments", {}),
                "id": getattr(call, "id", None),
            }
            calls_data.append(call_dict)

        return CustomEvent(
            data={
                "type": "tool_calls",
                "calls": calls_data,
            },
            metadata=self.extract_metadata(adk_event),
        )

    def _convert_function_responses(self, adk_event: Any) -> CustomEvent:
        function_responses = adk_event.get_function_responses()

        responses_data = []
        for response in function_responses:
            response_dict = {
                "name": getattr(response, "name", None),
                "response": getattr(response, "response", None),
                "id": getattr(response, "id", None),
            }
            responses_data.append(response_dict)

        return CustomEvent(
            data={
                "type": "tool_responses",
                "responses": responses_data,
            },
            metadata=self.extract_metadata(adk_event),
        )


__all__ = ["ToolEventHandler"]

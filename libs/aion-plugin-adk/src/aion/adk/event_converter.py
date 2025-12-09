from typing import Any, Optional

from aion.shared.agent.adapters import (
    CustomEvent,
    ExecutionEvent,
    MessageEvent,
    StateUpdateEvent,
)
from aion.shared.logging import get_logger

logger = get_logger()


class ADKEventConverter:

    def convert(
        self,
        adk_event: Any,
        metadata: Optional[Any] = None
    ) -> Optional[ExecutionEvent]:
        try:
            if self._is_streaming_chunk(adk_event):
                return self._convert_streaming_chunk(adk_event)

            if self._is_complete_message(adk_event):
                return self._convert_complete_message(adk_event)

            if self._has_function_calls(adk_event):
                return self._convert_function_calls(adk_event)

            if self._has_function_responses(adk_event):
                return self._convert_function_responses(adk_event)

            if self._has_state_updates(adk_event):
                return self._convert_state_update(adk_event)

            return self._convert_to_custom_event(adk_event)

        except Exception as e:
            logger.warning(f"Failed to convert ADK event: {e}")
            return None

    @staticmethod
    def _is_streaming_chunk(adk_event: Any) -> bool:
        """Check if event is a streaming chunk."""
        return hasattr(adk_event, "partial") and adk_event.partial

    @staticmethod
    def _is_complete_message(adk_event: Any) -> bool:
        """Check if event is a complete message."""
        return (
            hasattr(adk_event, "partial")
            and not adk_event.partial
            and hasattr(adk_event, "content")
            and adk_event.content is not None
        )

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

    def _convert_streaming_chunk(self, adk_event: Any) -> MessageEvent:
        content = str(adk_event.content) if hasattr(adk_event, "content") else ""
        author = adk_event.author if hasattr(adk_event, "author") else "assistant"

        return MessageEvent(
            data=content,
            role=author,
            is_streaming=True,
            metadata=self._extract_metadata(adk_event),
        )

    def _convert_complete_message(self, adk_event: Any) -> MessageEvent:
        content = str(adk_event.content)
        author = adk_event.author if hasattr(adk_event, "author") else "assistant"

        return MessageEvent(
            data=content,
            role=author,
            is_streaming=False,
            metadata=self._extract_metadata(adk_event),
        )

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
            metadata=self._extract_metadata(adk_event),
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
            metadata=self._extract_metadata(adk_event),
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
            metadata=self._extract_metadata(adk_event),
        )

    def _convert_to_custom_event(self, adk_event: Any) -> CustomEvent:
        event_data = {}
        for attr in ["content", "author", "id", "timestamp"]:
            if hasattr(adk_event, attr):
                event_data[attr] = getattr(adk_event, attr)

        return CustomEvent(
            data=event_data,
            metadata=self._extract_metadata(adk_event),
        )

    @staticmethod
    def _extract_metadata(adk_event: Any) -> dict[str, Any]:
        metadata = {
            "adk_type": type(adk_event).__name__,
        }

        for field in ["id", "invocation_id", "timestamp", "branch"]:
            if hasattr(adk_event, field):
                value = getattr(adk_event, field)
                if value is not None:
                    metadata[f"adk_{field}"] = value

        return metadata

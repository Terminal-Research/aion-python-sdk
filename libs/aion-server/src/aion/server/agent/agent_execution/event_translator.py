"""Event translator for converting ExecutionEvent to A2A events.

This module provides utilities for translating framework-agnostic ExecutionEvent
objects to A2A protocol events (Message, TaskStatusUpdateEvent, etc.).

ExecutionEvent is expected to contain already normalized data (simple types like
str, dict, list). Framework-specific normalization should happen in the
ExecutorAdapter layer before creating ExecutionEvent.
"""

import uuid
from typing import Optional

from a2a.types import (
    Message,
    Task,
    TextPart,
)
from aion.shared.agent.adapters import ExecutionEvent
from aion.shared.logging import get_logger

logger = get_logger()


class ExecutionEventTranslator:
    """Translates ExecutionEvent to A2A protocol events.

    This class handles the conversion of framework-agnostic ExecutionEvent
    objects (with already normalized data) into A2A-specific event types
    (primarily Message).

    Note: ExecutionEvent.data is expected to be already normalized by the
    ExecutorAdapter. This translator only handles ExecutionEvent → A2A mapping,
    NOT framework-specific type conversions.

    Internal events (node_update, state_update) are handled directly in
    AionAgentRequestExecutor and are NOT sent to the client.
    """

    @staticmethod
    def translate_message_event(
            execution_event: ExecutionEvent,
            task: Task,
    ) -> Optional[Message]:
        """Translate a 'message' ExecutionEvent to A2A Message.

        Args:
            execution_event: Event with type 'message' and normalized data
            task: Current task context

        Returns:
            A2A Message or None if translation not possible
        """
        event_data = execution_event.data
        metadata = execution_event.metadata or {}

        # Extract message content (data should already be normalized)
        if isinstance(event_data, str):
            message_content = event_data

        elif isinstance(event_data, dict):
            # Support dict format with 'content' or 'text' keys
            message_content = event_data.get("content") or event_data.get("text")
            if not message_content:
                logger.warning(
                    f"Dict event_data missing 'content' or 'text': {event_data}"
                )
                return None
        else:
            # Fallback: try to convert to string
            logger.warning(
                f"Unexpected event_data type {type(event_data).__name__}, "
                f"attempting string conversion"
            )
            try:
                message_content = str(event_data)
            except Exception as e:
                logger.error(f"Failed to convert event_data to string: {e}")
                return None

        if not message_content:
            logger.warning("Empty message content")
            return None

        # Create message parts
        parts: list[TextPart] = [TextPart(text=message_content)]

        # Determine role (default to "agent")
        # Map "assistant" to "agent" for A2A compatibility
        role = metadata.get("role", "agent")
        if role == "assistant":
            role = "agent"

        # Create A2A message
        message = Message(
            message_id=str(uuid.uuid4()),
            task_id=task.id,
            context_id=task.context_id,
            role=role,
            parts=parts,
        )

        logger.debug(
            f"Translated message event to A2A Message: "
            f"task_id={task.id}, role={role}, content_length={len(message_content)}"
        )
        return message

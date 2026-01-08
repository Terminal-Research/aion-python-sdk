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
from aion.shared.agent.adapters import ExecutionEvent, MessageEvent, MessagePartType
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
            execution_event: Event with type 'message' and MessagePart content
            task: Current task context

        Returns:
            A2A Message or None if translation not possible
        """
        # Validate that this is a MessageEvent with content field
        if not isinstance(execution_event, MessageEvent):
            logger.error(
                f"Expected MessageEvent, got {type(execution_event).__name__}"
            )
            return None

        # Extract MessagePart content
        content_parts = execution_event.content

        if not content_parts:
            logger.warning("Empty message content parts")
            return None

        # Convert MessageParts to A2A TextParts
        a2a_parts: list[TextPart] = []
        for part in content_parts:
            if part.type in (MessagePartType.TEXT, MessagePartType.THOUGHT):
                a2a_parts.append(TextPart(
                    text=part.content,
                    metadata=part.metadata or None
                ))

        if not a2a_parts:
            logger.debug(
                f"No text or thought parts found in message content "
                f"(content_parts={len(content_parts)})"
            )
            return None

        # Determine role from MessageEvent.role field
        # Normalize to A2A roles: 'agent' or 'user'
        role = execution_event.role or "agent"
        if role in ("assistant", "system"):
            role = "agent"
        elif role == "user":
            role = "user"
        else:
            # Any other role (e.g., agent names like 'clarification_handler') → 'agent'
            logger.debug(f"Normalizing non-standard role '{role}' to 'agent'")
            role = "agent"

        # Create A2A message
        message = Message(
            message_id=str(uuid.uuid4()),
            task_id=task.id,
            context_id=task.context_id,
            role=role,
            parts=a2a_parts,
        )

        return message

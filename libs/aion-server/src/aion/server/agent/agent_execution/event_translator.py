"""Event translator for converting ExecutionEvent to A2A events.

This module provides utilities for translating framework-agnostic ExecutionEvent
objects to A2A protocol events (Message, TaskStatusUpdateEvent, etc.).

ExecutionEvent now uses a2a Part types directly, minimizing conversion overhead.
"""

import uuid
from typing import Optional

from a2a.types import (
    Message,
    Part,
    Task,
    TextPart,
)
from aion.shared.agent.adapters import ExecutionEvent, MessageEvent
from aion.shared.logging import get_logger

logger = get_logger()


class ExecutionEventTranslator:
    """Translates ExecutionEvent to A2A protocol events.

    Since ExecutionEvent now uses a2a Part types directly, translation
    is simplified to filtering and role normalization.
    """

    @staticmethod
    def translate_message_event(
            execution_event: ExecutionEvent,
            task: Task,
    ) -> Optional[Message]:
        """Translate a 'message' ExecutionEvent to A2A Message.

        Filters out thought parts and keeps only text parts for the final message.

        Args:
            execution_event: MessageEvent with a2a Part content
            task: Current task context

        Returns:
            A2A Message or None if translation not possible
        """
        # Validate that this is a MessageEvent
        if not isinstance(execution_event, MessageEvent):
            logger.error(
                f"Expected MessageEvent, got {type(execution_event).__name__}"
            )
            return None

        # Extract Part content (already a2a types!)
        content_parts = execution_event.content

        if not content_parts:
            logger.warning("Empty message content parts")
            return None

        # Filter TextParts, exclude thoughts (metadata.thought == True)
        text_parts: list[Part] = []
        for part in content_parts:
            if isinstance(part.root, TextPart):
                # Skip thought parts
                if part.root.metadata and part.root.metadata.get("thought"):
                    continue
                text_parts.append(part)

        if not text_parts:
            logger.debug(
                f"No text parts found in message content "
                f"(content_parts={len(content_parts)})"
            )
            return None

        # Normalize role to A2A roles: 'agent' or 'user'
        role = execution_event.role or "agent"
        if role in ("assistant", "system", "agent"):
            role = "agent"
        elif role == "user":
            role = "user"
        else:
            logger.debug(f"Normalizing non-standard role '{role}' to 'agent'")
            role = "agent"

        # Create A2A message (parts are already a2a Part objects!)
        message = Message(
            message_id=str(uuid.uuid4()),
            task_id=task.id,
            context_id=task.context_id,
            role=role,
            parts=text_parts,
        )

        return message

"""Messages extractor for ADK sessions.

This module extracts conversation messages from ADK Session.events field
and converts them to unified Message format with a2a Part objects.
"""

from typing import Any, List, override

from a2a.types import Message
from aion.shared.agent.adapters import (
    StateExtractor,
    create_message_from_parts,
)
from aion.shared.logging import get_logger

from aion.adk.transformers import A2ATransformer

logger = get_logger()


class MessagesExtractor(StateExtractor):
    """Extractor for conversation messages from ADK Session.events field.

    Converts ADK events to unified Message format with a2a Part objects.
    Extracts only text and thought content from events, filtering out tool calls
    and tool responses.

    NOTE: ADK Session has an 'events' field (list[Event]) containing
    all events in the conversation (user messages, agent responses, tool calls).
    Even if an event contains both thoughts and tool calls, only the thoughts
    will be extracted.
    """

    @override
    def extract(self, session: Any) -> List[Message]:
        """Extract and convert ADK events to unified Message format.

        Args:
            session: ADK Session object

        Returns:
            List of unified Message objects representing the conversation history
        """
        if not self.can_extract(session):
            return []

        messages = []
        events = session.events

        logger.debug(f"Processing {len(events)} events from session")

        # Convert each ADK event to unified Message format
        for event in events:
            converted_messages = self._convert_event_to_messages(event)
            messages.extend(converted_messages)

        logger.debug(f"Converted {len(events)} events to {len(messages)} messages")
        return messages

    @override
    def can_extract(self, session: Any) -> bool:
        """Check if session has events.

        Args:
            session: ADK Session object

        Returns:
            bool: True if session has events
        """
        return (
            session is not None
            and hasattr(session, "events")
            and session.events is not None
            and len(session.events) > 0
        )

    def _convert_event_to_messages(self, adk_event: Any) -> List[Message]:
        """Convert a single ADK event to one or more unified Messages.

        NOTE: Tool calls and tool results are filtered out during content extraction.
        This method processes all events and extracts only text messages and thoughts,
        even if the event also contains function calls/responses.

        Args:
            adk_event: ADK event to convert

        Returns:
            List of Message objects (usually 1, may be multiple for complex events)
        """
        # Handle message events (A2ATransformer will filter out tool calls/responses)
        if self._is_message_event(adk_event):
            return self._handle_message(adk_event)

        # Fallback for other event types
        logger.debug(f"Skipping non-message event: {type(adk_event).__name__}")
        return []

    def _is_message_event(self, adk_event: Any) -> bool:
        """Check if event is a complete (non-streaming) message.

        Args:
            adk_event: ADK event to check

        Returns:
            bool: True if event is a message
        """
        return hasattr(adk_event, "content") and adk_event.content is not None

    def _handle_message(self, adk_event: Any) -> List[Message]:
        """Convert a regular message event to a2a Message format.

        Args:
            adk_event: ADK message event

        Returns:
            List containing a single a2a.Message object
        """
        author = getattr(adk_event, "author", "assistant")

        # Extract text from ADK event content using shared utility
        message_parts = A2ATransformer.transform_content(adk_event.content)

        if not message_parts:
            logger.debug(f"No parts extracted from content, skipping message")
            return []

        # Create a2a message
        message = create_message_from_parts(
            parts=message_parts,
            role=author,  # Helper will normalize to a2a.Role
            metadata=self._extract_event_metadata(adk_event),
        )

        return [message]

    def _extract_event_metadata(self, adk_event: Any) -> dict[str, Any]:
        """Extract metadata from ADK event.

        Args:
            adk_event: ADK event

        Returns:
            Dictionary of extracted metadata
        """
        metadata = {
            "adk_type": type(adk_event).__name__,
        }

        # Extract common ADK event fields if present
        if hasattr(adk_event, "id"):
            metadata["adk_id"] = adk_event.id
        if hasattr(adk_event, "invocation_id"):
            metadata["adk_invocation_id"] = adk_event.invocation_id
        if hasattr(adk_event, "timestamp"):
            metadata["adk_timestamp"] = adk_event.timestamp
        if hasattr(adk_event, "branch"):
            metadata["adk_branch"] = adk_event.branch

        return metadata


__all__ = ["MessagesExtractor"]

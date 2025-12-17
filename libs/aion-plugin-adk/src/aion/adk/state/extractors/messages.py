"""Messages extractor for ADK sessions.

This module extracts conversation messages from ADK Session.events field.
"""

from typing import Any, List, override

from aion.shared.agent.adapters import StateExtractor
from aion.shared.logging import get_logger

logger = get_logger()


class MessagesExtractor(StateExtractor):
    """Extractor for conversation messages from ADK Session.events field.

    Extracts the list of events that represent the conversation history.

    NOTE: ADK Session has an 'events' field (list[Event]) containing
    all events in the conversation (user messages, agent responses, tool calls).
    """

    @override
    def extract(self, session: Any) -> List[Any]:
        """Extract messages from Session.events.

        Args:
            session: ADK Session object

        Returns:
            List of events representing the conversation history
        """
        # Extract events from session.events field
        if hasattr(session, "events") and session.events:
            events = session.events
            logger.debug(f"Extracted {len(events)} events from session")
            return events

        return []

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
        )


__all__ = ["MessagesExtractor"]

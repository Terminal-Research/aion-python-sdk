"""Messages extractor for LangGraph snapshots.

This module extracts conversation messages from LangGraph StateSnapshot.values['messages']
and converts them to unified Message format (if needed in the future).
"""

from typing import Any, List, override

from aion.shared.agent.adapters import StateExtractor, Message
from aion.shared.logging import get_logger
from langgraph.types import StateSnapshot

logger = get_logger()


class MessagesExtractor(StateExtractor):
    """Extractor for conversation messages from LangGraph StateSnapshot.

    Extracts messages from snapshot.values['messages'] field.
    Currently returns raw LangGraph messages; can be extended to convert
    to unified Message format with a2a Part objects.

    NOTE: LangGraph typically stores messages in snapshot.values['messages']
    as a list of LangChain message objects (HumanMessage, AIMessage, etc.).
    """

    @override
    def extract(self, snapshot: StateSnapshot) -> List[Message]:
        """Extract messages from StateSnapshot.values['messages'].

        Args:
            snapshot: LangGraph StateSnapshot object

        Returns:
            List of messages (currently raw LangGraph messages,
            can be converted to unified Message format in the future)
        """
        if not self.can_extract(snapshot):
            return []

        # TODO: Convert LangGraph/LangChain messages to unified Message format
        # For now, return empty list as messages are handled elsewhere
        # In the future, implement conversion:
        # messages = snapshot.values.get("messages", [])
        # return [self._convert_to_unified_message(msg) for msg in messages]

        return []

    @override
    def can_extract(self, snapshot: StateSnapshot) -> bool:
        """Check if snapshot has messages.

        Args:
            snapshot: LangGraph StateSnapshot object

        Returns:
            bool: True if snapshot has messages in values
        """
        return (
            snapshot is not None
            and hasattr(snapshot, "values")
            and isinstance(snapshot.values, dict)
            and "messages" in snapshot.values
        )

    def _convert_to_unified_message(self, langgraph_message: Any) -> Message:
        """Convert LangGraph/LangChain message to unified Message format.

        TODO: Implement conversion from LangChain message types
        (HumanMessage, AIMessage, SystemMessage) to unified Message
        with a2a Part objects.

        Args:
            langgraph_message: LangGraph/LangChain message object

        Returns:
            Unified Message object
        """
        # Placeholder for future implementation
        raise NotImplementedError("Message conversion not yet implemented")


__all__ = ["MessagesExtractor"]

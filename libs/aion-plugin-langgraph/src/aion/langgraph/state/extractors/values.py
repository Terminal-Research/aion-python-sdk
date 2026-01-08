"""State values extractor for LangGraph snapshots.

This module extracts the state dictionary (values) from LangGraph StateSnapshot,
excluding messages which are handled separately.
"""

from typing import Any, Dict, override

from aion.shared.agent.adapters import StateExtractor
from aion.shared.logging import get_logger
from langgraph.types import StateSnapshot

logger = get_logger()


class StateValuesExtractor(StateExtractor):
    """Extractor for state values from LangGraph StateSnapshot.

    Extracts the state dictionary from snapshot.values, excluding the
    'messages' key which is handled by MessagesExtractor.

    NOTE: LangGraph StateSnapshot has a 'values' field (dict[str, Any])
    containing all state variables including messages. This extractor
    filters out messages to avoid duplication.
    """

    @override
    def extract(self, snapshot: StateSnapshot) -> Dict[str, Any]:
        """Extract state values from StateSnapshot.values, excluding messages.

        Args:
            snapshot: LangGraph StateSnapshot object

        Returns:
            Dict containing state values (without messages)
        """
        if not self.can_extract(snapshot):
            return {}

        # Extract all values except messages
        state = {
            k: v
            for k, v in snapshot.values.items()
            if k != "messages"
        }

        return state

    @override
    def can_extract(self, snapshot: StateSnapshot) -> bool:
        """Check if snapshot has values.

        Args:
            snapshot: LangGraph StateSnapshot object

        Returns:
            bool: True if snapshot has values dict
        """
        return (
            snapshot is not None
            and hasattr(snapshot, "values")
            and isinstance(snapshot.values, dict)
        )


__all__ = ["StateValuesExtractor"]

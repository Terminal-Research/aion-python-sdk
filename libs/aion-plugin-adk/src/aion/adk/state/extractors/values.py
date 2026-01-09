"""State values extractor for ADK sessions.

This module extracts the state dictionary (values) from ADK Session.state field.
"""

from typing import Any, Dict, override

from aion.shared.agent.adapters import StateExtractor
from aion.shared.logging import get_logger

logger = get_logger()


class StateValuesExtractor(StateExtractor):
    """Extractor for state values from ADK Session.state field.

    Extracts the state dictionary that contains arbitrary application state.

    NOTE: ADK Session has a 'state' field (dict[str, Any]) for storing
    persistent state between agent invocations.
    """

    @override
    def extract(self, session: Any) -> Dict[str, Any]:
        """Extract state values from Session.state.

        Args:
            session: ADK Session object

        Returns:
            Dict containing the state values from session.state
        """
        if not self.can_extract(session):
            return {}

        state_values = session.state
        return state_values

    @override
    def can_extract(self, session: Any) -> bool:
        """Check if session exists.

        Args:
            session: ADK Session object

        Returns:
            bool: Always True as any session can have state (even if empty)
        """
        return bool(hasattr(session, "state") and session.state)


__all__ = ["StateValuesExtractor"]

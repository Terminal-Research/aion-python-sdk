"""ADK state converter for converting ADK sessions to unified AgentState.

This module provides the StateConverter for extracting and converting
state information from ADK sessions into the unified AgentState format.
"""

from typing import Any

from aion.shared.agent.adapters import AgentState
from aion.shared.logging import get_logger

from .extractors import MessagesExtractor, StateValuesExtractor

logger = get_logger()


class StateConverter:
    """Converter for transforming ADK sessions to unified AgentState.

    Extracts state values and messages from ADK sessions and converts them
    into the unified AgentState format.

    NOTE: ADK does not expose interrupt state or next_steps through Session objects.
    All AgentState objects will have is_interrupted=False and next_steps=[].
    """

    def __init__(self):
        """Initialize converter with extractors."""
        self._values_extractor = StateValuesExtractor()
        self._messages_extractor = MessagesExtractor()

    def from_adk_session(self, session: Any) -> AgentState:
        """Convert ADK session to unified AgentState.

        Args:
            session: ADK Session object

        Returns:
            AgentState: Unified agent state with values and messages
                       (is_interrupted is always False, next_steps is always empty)
        """
        values = self._values_extractor.extract(session)
        messages = self._messages_extractor.extract(session)

        logger.debug(
            f"Converted ADK session to AgentState: "
            f"{len(values)} state keys, {len(messages)} messages"
        )

        return AgentState(
            values=values,
            next_steps=[],
            is_interrupted=False,
            messages=messages,
        )


__all__ = ["StateConverter"]

"""ADK state converter for converting ADK sessions to unified ExecutionSnapshot.

This module provides the StateConverter for extracting and converting
state information from ADK sessions into the unified ExecutionSnapshot format.
"""

from typing import Any

from aion.shared.agent.adapters import ExecutionSnapshot, ExecutionStatus
from aion.shared.logging import get_logger

from .extractors import MessagesExtractor, StateValuesExtractor

logger = get_logger()


class StateConverter:
    """Converter for transforming ADK sessions to unified ExecutionSnapshot.

    Extracts state values and messages from ADK sessions and converts them
    into the unified ExecutionSnapshot format.

    NOTE: ADK does not expose interrupt state or next_steps through Session objects.
    All ExecutionSnapshot objects will have status=COMPLETE and next_steps=[].
    """

    def __init__(self):
        """Initialize converter with extractors."""
        self._values_extractor = StateValuesExtractor()
        self._messages_extractor = MessagesExtractor()

    def from_adk_session(self, session: Any) -> ExecutionSnapshot:
        """Convert ADK session to unified ExecutionSnapshot.

        Args:
            session: ADK Session object

        Returns:
            ExecutionSnapshot: Unified execution snapshot with state values and messages
                              (status is always COMPLETE, next_steps is always empty)
        """
        execution_state = self._values_extractor.extract(session)
        messages = self._messages_extractor.extract(session)

        logger.debug(
            f"Converted ADK session to ExecutionSnapshot: "
            f"{len(execution_state)} state keys, {len(messages)} messages"
        )

        return ExecutionSnapshot(
            state=execution_state,
            messages=messages,
            status=ExecutionStatus.COMPLETE,
            metadata={},
        )


__all__ = ["StateConverter"]

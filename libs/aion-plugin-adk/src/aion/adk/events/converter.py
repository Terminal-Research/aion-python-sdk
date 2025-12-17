"""ADK event converter.

This module provides the ADKEventConverter for converting ADK events
to unified ExecutionEvent format using specialized handlers.
"""

from typing import Any, Optional

from aion.shared.agent.adapters import ExecutionEvent
from aion.shared.logging import get_logger

from .handlers import (
    CustomEventHandler,
    MessageEventHandler,
    StateUpdateEventHandler,
    ToolEventHandler,
)

logger = get_logger()


class ADKEventConverter:
    """Converter for ADK events to unified ExecutionEvent format.

    This converter uses a chain of specialized handlers to process
    different types of ADK events. Handlers are tried in order of
    specificity, with a fallback handler for unrecognized events.
    """

    def __init__(self):
        """Initialize converter with handlers.

        Handlers are ordered by specificity:
        1. MessageEventHandler - handles messages (most specific)
        2. ToolEventHandler - handles tool calls/responses
        3. StateUpdateEventHandler - handles state updates
        4. CustomEventHandler - handles everything else (fallback)
        """
        self._handlers = [
            MessageEventHandler(),
            ToolEventHandler(),
            StateUpdateEventHandler(),
            CustomEventHandler(),  # Fallback handler (always returns True)
        ]

    def convert(
        self,
        adk_event: Any,
        metadata: Optional[Any] = None
    ) -> Optional[ExecutionEvent]:
        """Convert ADK event to unified ExecutionEvent.

        Args:
            adk_event: ADK event to convert
            metadata: Optional additional metadata (not currently used)

        Returns:
            ExecutionEvent: Unified event, or None if conversion fails
        """
        try:
            for handler in self._handlers:
                if not handler.can_handle(adk_event):
                    continue

                result = handler.handle(adk_event)
                if result is not None:
                    logger.debug(
                        f"Converted ADK event using {type(handler).__name__}: "
                        f"{type(adk_event).__name__} > {type(result).__name__}"
                    )
                    return result

            logger.warning(f"No handler could convert ADK event: {type(adk_event).__name__}")
            return None

        except Exception as e:
            logger.warning(f"Failed to convert ADK event: {e}")
            return None


__all__ = ["ADKEventConverter"]

"""Pre-conversion processor for raw LangGraph events.

Handles LangGraph-specific side-effects (context tracking, logging, etc.)
before events are converted to A2A protocol format.
"""

from typing import Any

from aion.shared.agent.execution import set_langgraph_node
from aion.shared.logging import get_logger

logger = get_logger()


class LangGraphEventPreprocessor:
    """Processes raw LangGraph events for side-effects prior to A2A conversion.

    Responsibilities:
    - Track the currently executing node via set_langgraph_node ("updates" events)

    Extend this class to add further LangGraph-specific pre-processing
    without touching the A2A conversion logic.
    """

    def process(self, event_type: str, event_data: Any) -> None:
        """Process a raw LangGraph event for side-effects.

        Called for every event emitted by astream, before A2A conversion.
        Returns nothing — all effects are side-effects only.

        Args:
            event_type: LangGraph event type (messages, custom, values, updates).
            event_data: Raw event data as yielded by astream.
        """
        if event_type == "updates":
            self._handle_node_update(event_data)

    @staticmethod
    def _handle_node_update(event_data: Any) -> None:
        """Extract the active node name from an "updates" event and register it.

        LangGraph "updates" events are dicts keyed by node name. The first key
        is taken as the currently executing node and forwarded to
        set_langgraph_node so that downstream code (e.g. logging, tracing) can
        identify which node is running. Non-dict payloads are silently ignored.
        """
        if not isinstance(event_data, dict):
            return
        
        node_name = next(iter(event_data.keys()), None)
        if node_name:
            set_langgraph_node(node_name)
            logger.debug(f"Node: {node_name}")

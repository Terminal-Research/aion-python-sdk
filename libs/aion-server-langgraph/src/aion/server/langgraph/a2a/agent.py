from collections.abc import AsyncIterable

"""Currency conversion example agent built on top of LangGraph."""

from typing import Any, Dict, Union
import logging

from langgraph.graph import StateGraph
from langgraph.types import Command, StateSnapshot

from a2a.utils.errors import ServerError, InternalError

logger = logging.getLogger(__name__)


class LanggraphAgent:
    """Simple agent that delegates execution to a configured LangGraph."""

    def __init__(self, graph: StateGraph) -> None:
        """Initialize the agent using the first registered LangGraph."""
        self.graph = graph

    @staticmethod
    def _get_action_config(session_id: str):
        """Get config for agent invoke/stream action"""
        return {"configurable": {
            "thread_id": session_id
        }}

    async def invoke(self, query: Union[str, Command], session_id: str) -> dict[str, Any]:
        """Invoke the agent asynchronously.

        Args:
            query: The user message or a LangGraph Command
            session_id: Unique identifier for the conversation thread.

        Returns:
            The agent's final response as a string.
        """
        if isinstance(query, Command):
            inputs = query
        else:
            inputs = {"messages": [("user", query)]}

        config = self._get_action_config(session_id)
        await self.graph.ainvoke(inputs, config)
        return await self.get_agent_response(config)

    async def stream(
            self, query: Union[str, Command], session_id: str
    ) -> AsyncIterable[Dict[str, Any]]:
        """Stream intermediate responses from the agent.

        Args:
            query: The user message or a LangGraph Command
            session_id: Unique identifier for the conversation thread.

        Yields:
            Partial response dictionaries describing progress.
        """
        if isinstance(query, Command):
            inputs = query
        else:
            inputs = {"messages": [("user", query)]}

        config = self._get_action_config(session_id)
        logger.debug("Beginning Langgraph Stream: %s", inputs)
        try:
            async for eventType, event in self.graph.astream(
                    inputs, config, stream_mode=["values", "messages", "custom"]
            ):
                if eventType == "messages":
                    event, metadata = event
                else:
                    metadata = None

                logger.debug(
                    "Langgraph Stream Chunk [%s]:\n Event[%s]: %s\n Metadata[%s]: %s",
                    eventType,
                    type(event).__name__,
                    event,
                    type(metadata).__name__ if metadata is not None else "",
                    metadata if metadata is not None else "",
                )

                if (
                        eventType == "values"
                        or eventType == "messages"
                        or eventType == "custom"
                ):
                    yield {
                        "event_type": eventType,
                        "event": event,
                        "metadata": metadata,
                    }
                else:
                    raise ValueError(f"Unknown stream type: {eventType}")

            logger.debug("Final Langgraph Stream Chunk Received")
            yield await self.get_agent_response(config)

        except Exception as e:
            logger.error(
                f"An error occurred while processing Langgraph Stream Chunk: {e}"
            )
            raise ServerError(error=InternalError()) from e

    async def get_agent_response(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Return the final structured response from the agent."""
        current_state: StateSnapshot = await self.graph.aget_state(config)
        logger.debug("Final Langgraph State: %s", current_state)

        if len(current_state.tasks):
            for task in current_state.tasks:
                if hasattr(task, "interrupts") and len(task.interrupts):
                    logger.debug(f"Langgraph Interrupt occurred: {task.interrupts}")
                    return {
                        "event_type": "interrupt",
                        "event": task.interrupts,
                    }

        return {
            "event_type": "complete",
            "event": current_state,
        }

    SUPPORTED_CONTENT_TYPES = ["text", "text/plain"]

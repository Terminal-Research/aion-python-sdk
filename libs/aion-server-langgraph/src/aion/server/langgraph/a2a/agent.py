from collections.abc import AsyncIterable
"""Currency conversion example agent built on top of LangGraph."""

from typing import Any, Dict, Literal

import logging
import httpx

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from pydantic import BaseModel
from langgraph.graph import StateGraph

from ..graph import GRAPHS, get_graph, initialize_graphs

logger = logging.getLogger(__name__)


class ResponseFormat(BaseModel):
    """Respond to the user in this format."""

    status: Literal['input_required', 'completed', 'error'] = 'input_required'
    message: str


class LanggraphAgent:
    """Simple agent that delegates execution to a configured LangGraph."""

    def __init__(self, graph: StateGraph) -> None:
        """Initialize the agent using the first registered LangGraph."""
        self.graph = graph
        
    def invoke(self, query: str, sessionId: str) -> str:
        """Invoke the agent synchronously.

        Args:
            query: The user query to process.
            sessionId: Unique identifier for the conversation thread.

        Returns:
            The agent's final response as a string.
        """
        config = {"configurable": {"thread_id": sessionId}}
        self.graph.invoke({"messages": [("user", query)]}, config)
        return self.get_agent_response(config)

    async def stream(self, query: str, sessionId: str) -> AsyncIterable[Dict[str, Any]]:
        """Stream intermediate responses from the agent.

        Args:
            query: The user query to process.
            sessionId: Unique identifier for the conversation thread.

        Yields:
            Partial response dictionaries describing progress.
        """
        inputs = {"messages": [("user", query)]}
        config = {"configurable": {"thread_id": sessionId}}

        async for item in self.graph.astream(inputs, config, stream_mode='values'):
            logger.debug("Langgraph Stream Chunk Received: %s", item)
            message = item['messages'][-1]
            if (
                isinstance(message, AIMessage)
                and message.tool_calls
                and len(message.tool_calls) > 0
            ):
                yield {
                    'is_task_complete': False,
                    'require_user_input': False,
                    'content': 'Looking up the exchange rates...',
                }
            elif isinstance(message, ToolMessage):
                yield {
                    'is_task_complete': False,
                    'require_user_input': False,
                    'content': 'Processing the exchange rates..',
                }

        yield self.get_agent_response(config)

    def get_agent_response(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Return the final structured response from the agent."""
        current_state = self.graph.get_state(config)
        structured_response = current_state.values.get('structured_response')
        if structured_response and isinstance(
            structured_response, ResponseFormat
        ):
            if structured_response.status == 'input_required':
                return {
                    'is_task_complete': False,
                    'require_user_input': True,
                    'content': structured_response.message,
                }
            elif structured_response.status == 'error':
                return {
                    'is_task_complete': False,
                    'require_user_input': True,
                    'content': structured_response.message,
                }
            elif structured_response.status == 'completed':
                return {
                    'is_task_complete': True,
                    'require_user_input': False,
                    'content': structured_response.message,
                }

        return {
            'is_task_complete': False,
            'require_user_input': True,
            'content': 'We are unable to process your request at the moment. Please try again.',
        }

    SUPPORTED_CONTENT_TYPES = ['text', 'text/plain']

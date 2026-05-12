"""Transforms A2A RequestContext and ExecutionConfig into LangGraph format."""

from typing import Any, Optional, TYPE_CHECKING

from aion.shared.agent.adapters import ExecutionConfig
from langchain_core.messages import HumanMessage

from ..converters.a2a_to_lc import A2AToLcConverter

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext


class LangGraphTransformer:
    """Converts ExecutionConfig and RequestContext to LangGraph format.

    Stateless — all methods are static.
    """

    @staticmethod
    def generate_langgraph_config(config: Optional[ExecutionConfig]) -> dict[str, Any]:
        """Generate LangGraph config from ExecutionConfig.

        Maps context_id > thread_id.
        """
        if not config or not config.context_id:
            return {}
        return {"configurable": {"thread_id": config.context_id}}

    @staticmethod
    def generate_langgraph_inputs(context: "RequestContext") -> dict[str, Any]:
        """Generate LangGraph inputs from A2A RequestContext.

        Produces one key:
            messages — LangChain HumanMessage built from the inbound message parts.

        The full A2A context (task, message, event kind, identity) is passed to the
        graph separately via LangGraph's runtime context mechanism, not as a state field.
        """
        messages: list = []

        if context.message:
            content_blocks = A2AToLcConverter.from_parts(context.message.parts)
            if content_blocks:
                messages = [HumanMessage(content=content_blocks)]

        return {"messages": messages}

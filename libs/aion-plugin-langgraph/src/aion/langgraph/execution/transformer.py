"""Transforms A2A RequestContext and ExecutionConfig into LangGraph format."""

from typing import Any, Optional, TYPE_CHECKING

from aion.shared.agent.adapters import ExecutionConfig
from aion.shared.types.a2a import A2AInbox
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

        Produces two keys:
            messages    — LangChain HumanMessage built from the inbound message parts.
            a2a_inbox   — full a2a context for graphs that opt in by declaring
                           `a2a_inbox` in their state schema.  Contains:
                            task       – current Task object
                            message    – inbound a2a Message (full object)
                            metadata   – request-level metadata (network, trace, etc.)
        """
        messages: list = []

        if context.message:
            content_blocks = A2AToLcConverter.from_parts(context.message.parts)
            if content_blocks:
                messages = [HumanMessage(content=content_blocks)]

        return {
            "messages": messages,
            "a2a_inbox": A2AInbox.from_request_context(context),
        }

"""Transforms A2A RequestContext and ExecutionConfig into LangGraph format."""

from typing import Any, Optional, TYPE_CHECKING

from aion.server.agent.adapters import ExecutionConfig, is_state_key
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

        The checkpoint's ``thread_id`` is the agent, the owner and the A2A
        ``context_id`` together (``StateScope.key_for``): a client chooses
        its ``context_id``, and two users or two agents on one database may
        present the same one. The A2A ``context_id`` itself is unchanged -
        it is only the key under which the graph's state is saved.
        """
        if not config or not config.context_id:
            return {}
        scope = config.require_state_scope()
        return {"configurable": {"thread_id": scope.key_for(config.context_id)}}

    @staticmethod
    def legacy_langgraph_config(config: Optional[ExecutionConfig]) -> Optional[dict[str, Any]]:
        """The key state was saved under before it carried agent and owner, or None.

        That key was the ``context_id`` alone. None when there is no context,
        and when the ``context_id`` has the shape of a scoped key: a client
        could otherwise name another user's scoped key as its context and
        have it looked up as "legacy".
        """
        if not config or not config.context_id or is_state_key(config.context_id):
            return None
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

from typing import Any, Optional, TYPE_CHECKING

from aion.shared.agent.adapters import ExecutionConfig
from langchain_core.messages import HumanMessage

from .langchain_transformer import LangChainTransformer

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext


class LangGraphTransformer:
    """Transforms A2A types to LangGraph formats."""

    @staticmethod
    def to_config(config: Optional[ExecutionConfig]) -> dict[str, Any]:
        """Convert ExecutionConfig to LangGraph configuration format.

        Args:
            config: Execution configuration with context_id

        Returns:
            LangGraph config dict with thread_id (mapped from context_id)
        """
        if not config:
            return {}

        if not config.context_id:
            return {}

        return {"configurable": {"thread_id": config.context_id}}

    @staticmethod
    def to_inputs(
        context: "RequestContext",
        include_messages: bool,
        include_a2a_inbox: bool,
    ) -> dict[str, Any]:
        """Transform A2A RequestContext to LangGraph inputs format.

        Args:
            context: A2A request context
            include_messages: Whether to include messages in the output
            include_a2a_inbox: Whether to include a2a_inbox in the output

        Returns:
            LangGraph-compatible input dict
        """
        inputs: dict[str, Any] = {}

        # Handle messages (LLM-facing transcript)
        if context.message and include_messages:
            content_blocks = LangChainTransformer.to_content_blocks(context.message)
            if content_blocks:
                human_message = HumanMessage(
                    content=content_blocks,
                    id=context.message.message_id,  # For deduplication
                )
                inputs["messages"] = [human_message]

        # Store raw A2A envelope (transport-facing context)
        if include_a2a_inbox:
            inputs["a2a_inbox"] = {
                "task": context.current_task,
                "message": context.message,
                "metadata": context.metadata,
            }

        return inputs

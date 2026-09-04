"""LangGraph authoring toolkit for Aion agents.

Provides abstractions for building Aion-powered LangGraph agents:
- Event routing: Aion event dispatcher for graph nodes
- Models: LangChain chat model factory configured for Aion
- Threading: Thread and Message abstractions for agent-side streaming
- Emission: Helper functions for emitting events (messages, cards, artifacts, reactions)
- MCP tools: LangGraph-native MCP resolver and client factory
"""

from aion.core.utils.optional_deps import is_own_module, missing_extra_error

# This package ships in every wheel, but LangGraph and LangChain only arrive
# with the extra. Without them the import fails deep inside a third-party
# module, on a name that means nothing to whoever wrote the agent.
try:
    from .handlers import AionEventRouter, create_event_router
    from .mcp import (
        AionLangGraphMcpResolver,
        aion_langgraph_mcp_client,
        load_aion_mcp_tools,
    )
    from .models import aion_chat_model, aion_chat_openai
    from .invocation import Message, Thread
    from .invocation.emitters import (
        emit_artifact,
        emit_card,
        emit_message,
    )
except ModuleNotFoundError as exc:
    if is_own_module(exc.name):
        raise
    raise missing_extra_error(
        "LangGraph authoring", "langgraph-authoring", exc
    ) from exc

__all__ = [
    "AionEventRouter",
    "create_event_router",
    "AionLangGraphMcpResolver",
    "aion_langgraph_mcp_client",
    "aion_chat_model",
    "aion_chat_openai",
    "Message",
    "Thread",
    "emit_artifact",
    "emit_card",
    "emit_message",
    "load_aion_mcp_tools",
]

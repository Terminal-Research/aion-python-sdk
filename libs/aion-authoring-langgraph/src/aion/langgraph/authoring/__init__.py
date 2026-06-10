"""LangGraph authoring toolkit for Aion agents.

Provides abstractions for building Aion-powered LangGraph agents:
- Event routing: Aion event dispatcher for graph nodes
- Models: LangChain chat model factory configured for Aion
- Threading: Thread and Message abstractions for agent-side streaming
- Emission: Helper functions for emitting events (messages, cards, artifacts, reactions)
- MCP tools: LangGraph-native MCP resolver and client factory
"""

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
    emit_task_update,
)

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
    "emit_task_update",
    "load_aion_mcp_tools",
]

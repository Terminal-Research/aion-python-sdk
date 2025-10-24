"""Base adapter interfaces for framework integration.

This module provides abstract base classes that define the contracts for integrating
different agent frameworks (e.g., LangGraph, AutoGen, etc.) with the AION server.

The adapter architecture allows for flexible framework support by:
- Defining standard interfaces for framework-specific implementations
- Abstracting framework differences through unified adapters
- Enabling multiple frameworks to work together in the same server

Classes:
    AgentAdapter: Framework-specific agent lifecycle management
    ExecutorAdapter: Agent execution and streaming capabilities
    StateAdapter: Agent state extraction and management
    MessageAdapter: Message format translation and normalization
    CheckpointerAdapter: Agent state checkpoint and recovery

Registry:
    AdapterRegistry: Singleton registry for managing framework adapters

Singletons:
    adapter_registry: Global adapter registry instance
"""

from .agent_adapter import AgentAdapter
from .checkpointer_adapter import CheckpointerAdapter
from .executor_adapter import ExecutorAdapter
from .message_adapter import MessageAdapter
from .registry import AdapterRegistry, adapter_registry
from .state_adapter import StateAdapter

__all__ = [
    "AgentAdapter",
    "ExecutorAdapter",
    "StateAdapter",
    "MessageAdapter",
    "CheckpointerAdapter",
    "AdapterRegistry",
    "adapter_registry",
]

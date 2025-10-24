"""Core agent module for AION server.

This module provides the unified agent representation and management
for the AION server. It abstracts framework-specific details and provides
a consistent interface for working with agents.

Classes:
    AionAgent: Unified agent representation for all frameworks
    AgentMetadata: Agent runtime metadata
    AgentManager: Singleton manager for the single agent instance
    ModuleLoader: Utility for loading Python modules from various path formats
    AionAgentCard: Agent card representation

    Adapters:
        AgentAdapter: Framework-specific agent lifecycle management
        ExecutorAdapter: Agent execution and streaming capabilities
        StateAdapter: Agent state extraction and management
        MessageAdapter: Message format translation and normalization
        CheckpointerAdapter: Agent state checkpoint and recovery

    Registry:
        AdapterRegistry: Singleton registry for managing framework adapters

Singletons:
    agent_manager: Global agent manager instance
    adapter_registry: Global adapter registry instance
"""

# Core agent classes
from .aion_agent import (
    AionAgent,
    AgentManager,
    AgentMetadata,
    ModuleLoader,
    agent_manager,
)

# Agent card
from .card import AionAgentCard

# Adapters
from .adapters import (
    AgentAdapter,
    CheckpointerAdapter,
    ExecutorAdapter,
    MessageAdapter,
    StateAdapter,
    AdapterRegistry,
    adapter_registry,
)

# Exceptions
from .exceptions import (
    AdapterError,
    AdapterNotFoundError,
    AdapterRegistrationError,
    CheckpointError,
    ConfigurationError,
    ExecutionError,
    MessageConversionError,
    StateRetrievalError,
    UnsupportedOperationError,
)

__all__ = [
    # Core agent
    "AionAgent",
    "AgentMetadata",
    "AgentManager",
    "ModuleLoader",
    "agent_manager",
    # Agent card
    "AionAgentCard",
    # Adapters
    "AgentAdapter",
    "ExecutorAdapter",
    "StateAdapter",
    "MessageAdapter",
    "CheckpointerAdapter",
    "AdapterRegistry",
    "adapter_registry",
    # Exceptions
    "AdapterError",
    "AdapterNotFoundError",
    "AdapterRegistrationError",
    "ExecutionError",
    "StateRetrievalError",
    "CheckpointError",
    "MessageConversionError",
    "ConfigurationError",
    "UnsupportedOperationError",
]

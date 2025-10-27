"""Base adapter interfaces for framework integration.

This module provides abstract base classes that define the contracts for integrating
different agent frameworks (e.g., LangGraph, AutoGen, etc.) with the AION server.

The adapter architecture allows for flexible framework support by:
- Defining standard interfaces for framework-specific implementations
- Abstracting framework differences through unified adapters
- Enabling multiple frameworks to work together in the same server
"""

from .interfaces import (
    AgentAdapter,
    AgentState,
    Checkpoint,
    CheckpointerAdapter,
    CheckpointerConfig,
    CheckpointerType,
    ExecutionConfig,
    ExecutionEvent,
    ExecutorAdapter,
    InterruptInfo,
    MessageAdapter,
    MessageRole,
    MessageType,
    StateAdapter,
    StreamingArtifact,
    UnifiedMessage,
)
from .registry import AdapterRegistry, adapter_registry

__all__ = [
    # Interfaces
    "AgentAdapter",
    "AgentState",
    "Checkpoint",
    "CheckpointerAdapter",
    "CheckpointerConfig",
    "CheckpointerType",
    "ExecutionConfig",
    "ExecutionEvent",
    "ExecutorAdapter",
    "InterruptInfo",
    "MessageAdapter",
    "MessageRole",
    "MessageType",
    "StateAdapter",
    "StreamingArtifact",
    "UnifiedMessage",
    # Registry
    "AdapterRegistry",
    "adapter_registry",
]

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
    ExecutionSnapshot,
    ExecutionStatus,
    CompleteEvent,
    CustomEvent,
    ErrorEvent,
    ExecutionConfig,
    ExecutionEvent,
    ExecutorAdapter,
    InterruptEvent,
    InterruptInfo,
    MessageEvent,
    NodeUpdateEvent,
    StateUpdateEvent,
    StateExtractor,
    normalize_role_to_a2a,
    create_message_from_parts,
)
from .registry import AdapterRegistry, adapter_registry

__all__ = [
    # Interfaces
    "AgentAdapter",
    "ExecutionSnapshot",
    "ExecutionStatus",
    "CompleteEvent",
    "CustomEvent",
    "ErrorEvent",
    "ExecutionConfig",
    "ExecutionEvent",
    "ExecutorAdapter",
    "InterruptEvent",
    "InterruptInfo",
    "MessageEvent",
    "NodeUpdateEvent",
    "StateUpdateEvent",
    "StateExtractor",
    "normalize_role_to_a2a",
    "create_message_from_parts",
    # Registry
    "AdapterRegistry",
    "adapter_registry",
]

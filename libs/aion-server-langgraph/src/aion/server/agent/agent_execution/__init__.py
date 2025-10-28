"""Framework-agnostic agent execution module.

This module provides A2A protocol integration for AionAgent,
allowing framework-agnostic agent execution through adapters.

Architecture:
    AionAgentRequestExecutor (A2A handler)
        ↓ uses
    ExecutionEventHandler (routes events to specific handlers)
        ↓ uses
    ExecutionEventTranslator (ExecutionEvent → A2A)
        ↓ receives from
    AionAgent.stream() (returns ExecutionEvent)
        ↓ delegates to
    ExecutorAdapter (framework-specific, normalizes data)
"""

from .event_handler import ExecutionEventHandler
from .event_translator import ExecutionEventTranslator
from .request_executor import AionAgentRequestExecutor

__all__ = [
    "AionAgentRequestExecutor",
    "ExecutionEventHandler",
    "ExecutionEventTranslator",
]

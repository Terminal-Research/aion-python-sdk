"""Abstract base class for agent execution adapters.

This module defines ExecutorAdapter and related classes for executing agents
in a framework-agnostic way. It provides abstractions for:
- Synchronous and asynchronous agent invocation
- Streaming execution with events
- State retrieval and persistence
- Resume/recovery from interrupts
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any, Optional

from aion.shared.agent.inputs import AgentInput
from .events import ExecutionEvent
from .state import ExecutionSnapshot


class ExecutionConfig:
    """Configuration for a single agent execution.

    Attributes:
        task_id: Unique identifier for the specific task/execution (A2A Task.id)
        context_id: Context identifier for multi-turn conversations (A2A Task.context_id)
        timeout: Maximum execution time in seconds
        metadata: Additional execution metadata
    """
    def __init__(
        self,
        task_id: Optional[str] = None,
        context_id: Optional[str] = None,
        timeout: Optional[float] = None,
        metadata: Optional[dict[str, Any]] = None,
    ):
        """Initialize execution configuration.

        Args:
            task_id: Unique identifier for the specific task/execution (A2A Task.id)
            context_id: Context identifier for multi-turn conversations (A2A Task.context_id)
            timeout: Maximum execution time in seconds
            metadata: Additional execution metadata
        """
        self.task_id = task_id
        self.context_id = context_id
        self.timeout = timeout
        self.metadata = metadata or {}


class ExecutorAdapter(ABC):
    """Abstract base for framework-specific agent execution.

    Subclasses must implement all abstract methods to provide framework-specific
    execution, streaming, and state management capabilities.

    The ExecutorAdapter handles:
    - Streaming execution with real-time events
    - State retrieval and management
    - Resume/recovery from interrupts

    Note: This adapter is designed for A2A protocol which always uses streaming
    execution. All execution flows use the stream() method to generate events
    that are then either streamed to the client (message/stream) or aggregated
    into a final Task object (message/send).
    """
    @abstractmethod
    async def stream(
        self,
        inputs: AgentInput,
        config: Optional[ExecutionConfig] = None,
    ) -> AsyncIterator[ExecutionEvent]:
        """Stream agent execution, yielding events in real-time.

        Args:
            inputs: Universal agent input (will be transformed to framework format)
            config: Execution configuration (task_id, context_id, timeout, etc.)

        Yields:
            ExecutionEvent: Events emitted during execution (e.g., tokens, tool calls)

        Raises:
            TimeoutError: If execution exceeds configured timeout
            Exception: Any framework-specific errors during execution
        """
        pass

    @abstractmethod
    async def get_state(self, config: ExecutionConfig) -> ExecutionSnapshot:
        """Retrieve the current execution state snapshot.

        Args:
            config: Execution configuration specifying which execution to retrieve

        Returns:
            ExecutionSnapshot: Current execution snapshot including state, messages, status, and metadata

        Raises:
            KeyError: If execution not found
        """
        pass

    @abstractmethod
    async def resume(
        self,
        inputs: Optional[AgentInput],
        config: ExecutionConfig,
    ) -> AsyncIterator[ExecutionEvent]:
        """Resume a paused/interrupted agent execution.

        Args:
            inputs: Universal agent input to provide after interruption (optional)
            config: Execution configuration specifying which execution to resume

        Yields:
            ExecutionEvent: Events emitted during resumed execution

        Raises:
            KeyError: If execution not found
            ValueError: If execution is not in a resumable state
        """
        pass

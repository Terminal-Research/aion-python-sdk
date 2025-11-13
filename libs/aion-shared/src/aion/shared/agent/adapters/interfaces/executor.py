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
from .state import AgentState


class ExecutionConfig:
    """Configuration for a single agent execution.

    Attributes:
        session_id: Unique identifier for the execution session
        thread_id: Thread identifier for multi-turn conversations
        timeout: Maximum execution time in seconds
        metadata: Additional execution metadata
    """
    def __init__(
        self,
        session_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        timeout: Optional[float] = None,
        metadata: Optional[dict[str, Any]] = None,
    ):
        """Initialize execution configuration.

        Args:
            session_id: Unique identifier for the execution session
            thread_id: Thread identifier for multi-turn conversations
            timeout: Maximum execution time in seconds
            metadata: Additional execution metadata
        """
        self.session_id = session_id
        self.thread_id = thread_id
        self.timeout = timeout
        self.metadata = metadata or {}


class ExecutorAdapter(ABC):
    """Abstract base for framework-specific agent execution.

    Subclasses must implement all abstract methods to provide framework-specific
    execution, streaming, and state management capabilities.

    The ExecutorAdapter handles:
    - Synchronous and asynchronous agent invocation
    - Streaming execution with real-time events
    - State retrieval and management
    - Resume/recovery from interrupts
    """
    @abstractmethod
    async def invoke(
        self,
        inputs: AgentInput,
        config: Optional[ExecutionConfig] = None,
    ) -> dict[str, Any]:
        """Execute the agent with given inputs and return final output.

        Args:
            inputs: Universal agent input (will be transformed to framework format)
            config: Execution configuration (session_id, thread_id, etc.)

        Returns:
            dict[str, Any]: Final agent output

        Raises:
            TimeoutError: If execution exceeds configured timeout
            Exception: Any framework-specific errors during execution
        """
        pass

    @abstractmethod
    async def stream(
        self,
        inputs: AgentInput,
        config: Optional[ExecutionConfig] = None,
    ) -> AsyncIterator[ExecutionEvent]:
        """Stream agent execution, yielding events in real-time.

        Args:
            inputs: Universal agent input (will be transformed to framework format)
            config: Execution configuration (session_id, thread_id, etc.)

        Yields:
            ExecutionEvent: Events emitted during execution (e.g., tokens, tool calls)

        Raises:
            TimeoutError: If execution exceeds configured timeout
            Exception: Any framework-specific errors during execution
        """
        pass

    @abstractmethod
    async def get_state(self, config: ExecutionConfig) -> AgentState:
        """Retrieve the current state of the agent execution.

        Args:
            config: Execution configuration specifying which execution to retrieve

        Returns:
            AgentState: Current agent state including values, next steps, and interrupts

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

    def supports_streaming(self) -> bool:
        """Check if this executor supports streaming execution."""
        return False

    def supports_resume(self) -> bool:
        """Check if this executor supports resuming interrupted executions."""
        return False

    def supports_state_retrieval(self) -> bool:
        """Check if this executor supports state retrieval."""
        return False

    def supports_multi_turn(self) -> bool:
        """Check if executor supports multi-turn conversations."""
        return False

    def supports_parallel_execution(self) -> bool:
        """Check if executor supports parallel execution."""
        return False

    def supports_tool_calling(self) -> bool:
        """Check if executor supports tool calling."""
        return False

    def supports_human_in_loop(self) -> bool:
        """Check if executor supports human-in-the-loop."""
        return False

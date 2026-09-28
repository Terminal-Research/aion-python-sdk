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
from dataclasses import dataclass
from typing import Any, Optional, TYPE_CHECKING
from urllib.parse import quote


from a2a.types import TaskArtifactUpdateEvent, TaskStatusUpdateEvent
from a2a.utils.errors import UnsupportedOperationError

from .state import ExecutionSnapshot

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext
    from a2a.server.context import ServerCallContext
    from a2a.server.owner_resolver import OwnerResolver


STATE_KEY_PREFIX = "aion.v1"
"""Marks a framework state key built by ``StateScope.key_for``."""


class LegacyStateError(Exception):
    """A context holds framework state saved before keys carried agent and owner.

    Such state was keyed by the A2A ``context_id`` alone, so it cannot be
    attributed to the user now asking for it: another user may have chosen
    the same ``context_id``. It is neither read nor replaced; the turn is
    refused, and the state stays where it is.
    """


@dataclass(frozen=True)
class StateScope:
    """Whose framework state one execution reads and writes.

    ``context_id`` names a conversation only as the client sees it: clients
    choose it, so two users - or two agents on one database - can present the
    same one. The state a framework keeps for the conversation - a LangGraph
    checkpoint, an ADK session - is therefore keyed by the agent and the user
    as well, the way the tasks table keys its rows.

    Built per call from the request, never kept on a shared executor.

    Attributes:
        agent_id: The Aion agent id, the ``agent_id`` of the tasks table.
        owner_scope: The owner of the request's ``ServerCallContext``,
            resolved by the same resolver as the agent's task store. Not a lease owner: that
            is a server process and has nothing to do with whose state this is.
    """

    agent_id: str
    owner_scope: str

    @classmethod
    def for_call(
            cls,
            agent_id: str,
            call_context: "ServerCallContext",
            owner_resolver: "OwnerResolver",
    ) -> "StateScope":
        """The scope of one request, from its call context.

        ``owner_resolver`` is the one the agent's task store uses - taken
        from ``AionAgent.owner_resolver`` - so a task and the framework state
        beside it resolve the same owner. It is required rather than
        defaulted: a default here is exactly how the two could drift apart.
        """
        if call_context is None:
            raise ValueError("a state scope needs the caller's ServerCallContext")
        return cls(agent_id=agent_id, owner_scope=owner_resolver(call_context))

    def key_for(self, context_id: str) -> str:
        """One string key for this agent, owner and context.

        Each part is percent-encoded, so no choice of agent id, user name or
        context id can make two different triples meet in one key.
        """
        parts = (self.agent_id, self.owner_scope, context_id)
        return ":".join([STATE_KEY_PREFIX, *(quote(part, safe="") for part in parts)])


def is_state_key(value: str) -> bool:
    """Whether ``value`` has the shape of a key ``StateScope.key_for`` builds."""
    return value.startswith(f"{STATE_KEY_PREFIX}:")


class ExecutionConfig:
    """Configuration for a single agent execution.

    Attributes:
        task_id: Unique identifier for the specific task/execution (A2A Task.id)
        context_id: Context identifier for multi-turn conversations (A2A Task.context_id)
        timeout: Maximum execution time in seconds
        metadata: Additional execution metadata
        state_scope: Whose framework state this execution reads and writes.
            Required by every executor that keeps state; see ``StateScope``.
    """
    def __init__(
        self,
        task_id: Optional[str] = None,
        context_id: Optional[str] = None,
        timeout: Optional[float] = None,
        metadata: Optional[dict[str, Any]] = None,
        state_scope: Optional[StateScope] = None,
    ):
        """Initialize execution configuration.

        Args:
            task_id: Unique identifier for the specific task/execution (A2A Task.id)
            context_id: Context identifier for multi-turn conversations (A2A Task.context_id)
            timeout: Maximum execution time in seconds
            metadata: Additional execution metadata
            state_scope: Whose framework state this execution uses.
        """
        self.task_id = task_id
        self.context_id = context_id
        self.timeout = timeout
        self.metadata = metadata or {}
        self.state_scope = state_scope

    def require_state_scope(self) -> StateScope:
        """The scope, or an error: framework state is never read unscoped."""
        if self.state_scope is None:
            raise ValueError(
                "this execution carries no state scope; framework state is keyed by "
                "agent and owner and is not read by context_id alone"
            )
        return self.state_scope


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
        context: "RequestContext",
        config: Optional[ExecutionConfig] = None,
    ) -> AsyncIterator[TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
        """Stream agent execution, yielding A2A events in real-time.

        Args:
            context: A2A request context with message, metadata, and task information
            config: Execution configuration (task_id, context_id, timeout, etc.)

        Yields:
            AgentEvent: A2A events emitted during execution

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
        context: "RequestContext",
        config: ExecutionConfig,
    ) -> AsyncIterator[TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
        """Resume a paused/interrupted agent execution.

        Args:
            context: A2A request context with message, metadata, and task information
            config: Execution configuration specifying which execution to resume

        Yields:
            AgentEvent: A2A events emitted during resumed execution

        Raises:
            KeyError: If execution not found
            ValueError: If execution is not in a resumable state
        """
        pass

    async def cancel(self, config: ExecutionConfig) -> None:
        """Framework-specific cancellation hook.

        Override to interrupt active execution (cancel asyncio tasks, set stop flags,
        release resources, etc.). Called before the A2A CANCELED state is emitted.

        Args:
            config: Execution configuration identifying the task to cancel

        Raises:
            UnsupportedOperationError: Default — framework does not support cancellation.
        """
        raise UnsupportedOperationError()

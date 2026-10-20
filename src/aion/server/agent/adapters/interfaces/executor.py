"""The executors the server runs tasks with, and what one execution is told.

``TaskExecutor`` is the API every executor has: ``stream``, ``resume`` and
``cancel``. ``AgentFrameworkExecutor`` is the agent's framework executor,
which also keeps framework state keyed by agent and owner (``StateScope``)
and deletes it; an executable extension's executor is
``aion.server.agent.execution.extensions.ExtensionExecutor``.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, ClassVar, Literal, Optional, TYPE_CHECKING
from urllib.parse import quote


from a2a.server.events import Event
from a2a.types import TaskArtifactUpdateEvent, TaskStatusUpdateEvent
from a2a.utils.errors import UnsupportedOperationError

from aion.server.auth import CredentialKind, verified_caller

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


GATEWAY_STATE_OWNER_PREFIX = "aion.gateway"
"""Marks the state owner of a gateway conversation, as ``StateScope.state_owner`` spells it."""


@dataclass(frozen=True)
class StateScope:
    """Whose framework state one execution reads and writes.

    ``context_id`` names a conversation only as the client sees it: clients
    choose it, so two users - or two agents on one database - can present the
    same one. The state a framework keeps for the conversation - a LangGraph
    checkpoint, an ADK session - is therefore keyed by the agent and the
    conversation's state owner as well, the way the tasks table keys its rows.

    The state owner is not always the task's owner. A private conversation -
    an anonymous session, the application's own user - is its caller's alone,
    and its state owner is that caller's ``owner_scope``. A conversation Aion
    routes through its gateway is shared by every participant of one receiving
    agent identity at one edge environment: its state is keyed by those
    (``gateway``), whoever started the turn, while each task still belongs to
    the participant who started it. Context admission keeps a context one kind
    or the other, so the two kinds never meet in one key.

    Built per call from the request, never kept on a shared executor.

    Attributes:
        agent_id: The Aion agent id, the ``agent_id`` of the tasks table.
        owner_scope: The owner of the request's ``ServerCallContext``,
            resolved by the same resolver as the agent's task store; the state
            owner of a private conversation. Which server process runs the
            task has nothing to do with whose state this is.
        gateway: For a verified Aion invocation, its receiving agent
            identity and edge environment: the shared conversation's state
            owner. ``None`` for a private conversation.
    """

    agent_id: str
    owner_scope: str
    gateway: Optional[tuple[str, str]] = None

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
        Only a verified invocation token makes the scope shared - never
        request metadata.
        """
        if call_context is None:
            raise ValueError("a state scope needs the caller's ServerCallContext")
        caller = verified_caller(call_context)
        gateway = None
        if caller is not None and caller.credential is CredentialKind.INVOCATION:
            gateway = (caller.gateway.owner_agent_identity_id, caller.gateway.edge_agent_environment_id)
        return cls(agent_id=agent_id, owner_scope=owner_resolver(call_context), gateway=gateway)

    @property
    def state_owner(self) -> str:
        """Whose the conversation's state is: the private owner, or the gateway conversation."""
        if self.gateway is None:
            return self.owner_scope
        return ":".join([GATEWAY_STATE_OWNER_PREFIX, *(quote(part, safe="") for part in self.gateway)])

    def key_for(self, context_id: str) -> str:
        """One string key for this agent, state owner and context.

        Each part is percent-encoded, so no choice of agent id, user name or
        context id can make two different triples meet in one key.
        """
        parts = (self.agent_id, self.state_owner, context_id)
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


class TaskExecutor(ABC):
    """Runs the turns of A2A tasks: the one API the server calls every executor through.

    The server keeps a pool of executors - the agent's framework executor
    (``AgentFrameworkExecutor``) and one per executable extension the agent
    enables (``ExtensionExecutor``) - and calls whichever a task belongs to
    the same way: ``stream`` for a new task, ``resume`` for a message that
    continues one, ``cancel`` to stop its running turn. A turn yields A2A
    events, which go the standard way (``AionEventPipeline``); the server
    owns the turn around them: a turn that raises fails the task, and the
    final status of a cancel or a shutdown is the server's.

    One instance serves every task, concurrently.

    Attributes:
        message_to_busy_task: What a message into a task of this executor
            gets while the task's turn runs (``SUBMITTED``, ``WORKING``).
            ``"queue"`` admits it, as a2a-sdk does: it becomes the task's
            next turn (``resume``), after the running one on the server
            running it and alongside it on another server, where the task's
            version decides which writes land; it never reaches the running
            turn. ``"refuse"`` refuses it before the task changes, with the
            JSON-RPC error ``task_busy`` naming the task. A task waiting
            for input takes a message either way.
    """

    message_to_busy_task: ClassVar[Literal["queue", "refuse"]] = "queue"

    @abstractmethod
    async def stream(
        self,
        context: "RequestContext",
        config: ExecutionConfig,
    ) -> AsyncIterator[TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
        """Run the first turn of a new task, yielding A2A events.

        Args:
            context: A2A request context with the message and the task.
            config: The task, its context and whose framework state the turn
                uses.
        """
        pass

    @abstractmethod
    async def resume(
        self,
        context: "RequestContext",
        config: ExecutionConfig,
    ) -> AsyncIterator[TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
        """Run the next turn of a task, yielding A2A events.

        Args:
            context: A2A request context with the message and the task.
            config: The task, its context and whose framework state the turn
                uses.
        """
        pass

    async def cancel(self, config: ExecutionConfig, *, within: Optional[float] = None) -> None:
        """Stop the turn running for ``config.task_id`` in this process.

        Called for a caller's cancel and at a server shutdown. The server
        then waits up to ``within`` seconds for the turn to end and writes
        the task's final status itself: ``CANCELED``, or ``FAILED`` coded
        ``server_shutdown``. What the turn yields while it stops reaches the
        task first. Idempotent; a task with no turn here is not an error.

        Args:
            config: Identifies the task.
            within: How long the server waits for the turn to end; None when
                it waits for none.

        Raises:
            UnsupportedOperationError: Default - the executor cannot stop a
                turn, and the server does not wait for it.
        """
        raise UnsupportedOperationError()


class AgentFrameworkExecutor(TaskExecutor):
    """The executor of the agent's framework (LangGraph, ADK, ...), created by its plugin.

    It serves every task no executable extension takes. Besides the turns,
    it owns the framework's state of a context - a LangGraph checkpoint, an
    ADK session - kept outside the task, which ``DeleteContext`` removes
    through :meth:`delete_state`.

    Attributes:
        framework: The framework's name, as its plugin's adapter names it
            (``AgentAdapter.framework_name``).
    """

    framework: ClassVar[str] = ""

    @property
    def supports_state_deletion(self) -> bool:
        """Whether :meth:`delete_state` can remove this executor's framework state.

        ``DeleteContext`` asks before it changes anything, so a context whose
        state could not be removed is refused up front rather than half
        deleted. Defaults to whether :meth:`delete_state` is overridden.
        """
        return type(self).delete_state is not AgentFrameworkExecutor.delete_state

    async def delete_state(self, config: ExecutionConfig) -> None:
        """Delete the framework state kept for ``config.context_id`` under ``config.state_scope``.

        Called by ``DeleteContext`` once every task of the context is
        terminal, so nothing is running against the state. Must be
        idempotent: state that does not exist, or no longer does, is not an
        error - a retried deletion calls this again.

        Args:
            config: Identifies the context; ``state_scope`` names its state
                owner the same way the executions that wrote it did.

        Raises:
            UnsupportedOperationError: Default - the framework keeps state
                this executor cannot delete.
        """
        raise UnsupportedOperationError()

    async def discard_undelivered(
        self,
        config: ExecutionConfig,
        event: Event,
    ) -> None:
        """Forget what the framework kept of an event the server could not deliver.

        Called when an event this executor produced never reached the task:
        a file in it could not be stored, so the task fails instead. A
        framework that keeps its own copy of what it emits, such as an
        artifact store, removes that copy here, so a later turn of the context
        cannot read back what the client never received. Failures are logged
        by the caller and do not change the task's outcome.

        Args:
            config: Identifies the task and context under the same state scope
                as the execution that produced the event.
            event: The event as the executor produced it.
        """
        return None

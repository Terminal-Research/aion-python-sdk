"""Framework-agnostic extension routing for A2A requests.

Defines the ExtensionTaskHandler contract that lets an A2A extension take
over stream()/resume()/cancel() of a task in place of the agent's framework
adapter (LangGraph, ADK, ...), based solely on which A2A extensions are
declared active on the request that starts the task. Framework adapters never
participate in this decision. Every handler the server runs drives one
toolkit plugin (see ``toolkit``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional, Protocol

from a2a.types import Message, TaskArtifactUpdateEvent, TaskStatusUpdateEvent

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext
    from a2a.server.context import ServerCallContext
    from a2a.server.events import EventQueue
    from aion.core.runtime.context.extensions import AionRuntimeExtensions

__all__ = [
    "EXTENSION_ROUTE_STATE_KEY",
    "ExtensionRoute",
    "ExtensionTaskHandler",
]

EXTENSION_ROUTE_STATE_KEY = "aion:extensionRoute"
"""Key of the ``ServerCallContext.state`` entry that carries an admitted route.

The request handler decides and admits the route of a new task before the
task exists, and the executor reads it here: the decision is made once.
"""


@dataclass(frozen=True)
class ExtensionRoute:
    """A new task's route to an extension handler, decided before the task exists.

    Attributes:
        uri: The extension URI of the handler the task belongs to.
        admission: What the handler's ``admit`` returned; handed to its
            ``stream``.
    """

    uri: str
    admission: Any = None


class ExtensionTaskHandler(Protocol):
    """Alternate stream()/resume()/cancel() provider for a single A2A extension.

    Implementations are framework-agnostic: they emit A2A events directly,
    without going through the agent's framework adapter. A request that starts
    a new task is routed to the handler whose ``uri`` it declares active.
    """

    uri: str
    """Extension URI this handler owns."""

    async def admit(
        self,
        message: Message,
        extensions: "AionRuntimeExtensions",
        call_context: "ServerCallContext",
    ) -> Any:
        """Check a request that would start a task of this handler, before the task exists.

        Called by the request handler after the caller is admitted into the
        context and the request's extensions are verified, and before the
        task is created; a refusal here leaves nothing behind.

        Args:
            message: The user message; its ``context_id`` is set.
            extensions: The request's verified extensions.
            call_context: The request's call context.

        Returns:
            What ``stream`` needs of the admitted request.

        Raises:
            a2a.utils.errors.A2AError: The request is refused; the caller
                gets this JSON-RPC error.
        """
        ...

    def stream(
        self, context: "RequestContext", admission: Any
    ) -> AsyncIterator[TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
        """Run the first turn of a new task routed to this extension."""
        ...

    def resume(
        self, context: "RequestContext"
    ) -> AsyncIterator[TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
        """Run the next turn of a task routed to this extension."""
        ...

    async def cancel(
        self, context: "RequestContext", event_queue: "EventQueue"
    ) -> Optional["Message"]:
        """Cancel a task routed to this extension.

        Called in place of the agent's framework-adapter cancel hook when the
        task being cancelled was routed to this handler. The executor writes
        the terminal CANCELED after it returns; a returned message becomes
        that status's message. Raise UnsupportedOperationError (see
        a2a.utils.errors) when there is nothing to stop - the executor still
        writes CANCELED.
        """
        ...

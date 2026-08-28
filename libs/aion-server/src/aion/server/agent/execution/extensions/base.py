"""Framework-agnostic extension routing for A2A requests.

Defines the ExtensionTaskHandler contract that lets an A2A extension (e.g. a
future self-improvement/evolution flow) take over stream()/resume() for a
task in place of the agent's framework adapter (LangGraph, ADK, ...), based
solely on which A2A extensions are declared active on the inbound request.
Framework adapters never participate in this decision.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Optional, Protocol

from a2a.types import Message, TaskArtifactUpdateEvent, TaskStatusUpdateEvent

from .availability import ExtensionAvailability
from .errors import ExtensionPreflightError
from .evolution import EvolutionTaskHandler

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext
    from aion.core.config.models import AgentConfig

__all__ = [
    "ExtensionAvailability",
    "ExtensionPreflightError",
    "ExtensionTaskHandler",
    "discover_extension_task_handlers",
    "ROUTED_EXTENSION_METADATA_KEY",
]

# Platform-owned task metadata key recording which extension handler a task
# was routed to at creation time. Prefixed under the platform metadata
# namespace (see aion.server.tasks.deduplicator.PLATFORM_METADATA_PREFIX) so
# A2ATaskDeduplicator protects it from being overwritten by incoming task
# patches - the routing decision must persist unchanged for the task's
# lifetime, see resume-routing rationale in
# TODO: this concept is not yet finalized - reconcile it with the extension
# docs once the design settles.
ROUTED_EXTENSION_METADATA_KEY = "https://docs.aion.to/a2a/task#routedExtension"


class ExtensionTaskHandler(Protocol):
    """Alternate stream()/resume() provider for a single A2A extension.

    Implementations are framework-agnostic: they drive their own toolkit and
    emit A2A events directly, without going through the agent's framework
    adapter. Registered handlers are matched by whether their `uri` is
    declared active on the inbound request's runtime context.
    """

    uri: str
    """Extension URI this handler owns (matched against message.extensions[])."""

    async def availability(self, config: "AgentConfig") -> ExtensionAvailability:
        """Report whether this handler can run for the given agent config.

        Called once per executor instance at startup, e.g. to check that a
        required optional toolkit dependency is installed. When unavailable,
        the returned reason is surfaced verbatim to clients whose requests
        declare this extension - make it actionable.
        """
        ...

    async def preflight(self, context: "RequestContext") -> None:
        """Reject this specific request before its task is created.

        Called once per request, after routing but before the task is
        persisted - see ExtensionPreflightError. Raise it (or a subclass) to
        reject; anything else is treated as success. The default is a no-op:
        a handler with nothing to check per-request need not implement this
        at all - the executor calls it via `getattr(handler, "preflight",
        None)`, so an absent override is the same as one that always passes.
        """
        return None

    def stream(
        self, context: "RequestContext"
    ) -> AsyncIterator[TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
        """Start a new task routed to this extension."""
        ...

    def resume(
        self, context: "RequestContext"
    ) -> AsyncIterator[TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
        """Resume an interrupted task previously routed to this extension."""
        ...

    async def cancel(self, context: "RequestContext") -> Optional["Message"]:
        """Cancel a task previously routed to this extension.

        Called in place of the agent's framework-adapter cancel hook when the
        task being canceled was routed to this handler. Raise
        UnsupportedOperationError (see a2a.utils.errors) if this handler does
        not support cancellation - the executor still emits the terminal A2A
        CANCELED state either way.

        May return a message for the executor to attach to that terminal
        CANCELED. It is the only way a handler can report anything about a
        cancelled task: the event consumer shuts down on the first terminal
        state, so an event published after the CANCELED is dropped rather than
        delivered late. A handler with nothing to add returns None, leaving the
        CANCELED bare.
        """
        ...


# Explicit, known extension handler classes. See
# Uses BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1 (behaviour/evolution namespace).
_KNOWN_TASK_HANDLERS: list[type[ExtensionTaskHandler]] = [EvolutionTaskHandler]


def discover_extension_task_handlers() -> list[ExtensionTaskHandler]:
    """Instantiate all known extension task handlers."""
    return [cls() for cls in _KNOWN_TASK_HANDLERS]

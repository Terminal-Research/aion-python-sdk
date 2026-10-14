"""How a toolkit plugs into the Aion server.

A toolkit is a package that registers one object under the ``aion.toolkits``
entry point group. The server finds it at startup, reads its
:class:`ToolkitDescriptor`, checks its settings and availability, and serves
every request that activates the toolkit's extension through one generic
handler: it routes the request, runs :meth:`Toolkit.preflight` before a task
exists, drives :meth:`Toolkit.start` or :meth:`Toolkit.resume` and translates
what they yield into A2A, and calls :meth:`Toolkit.cancel` when the task is
cancelled or the server stops.

Installing a toolkit does not expose it: the agent's ``enabled_extensions``
must list its URI. A toolkit that fails to import, has invalid settings or
speaks an incompatible protocol version is unavailable, with the reason in
the log; requests that declare it get ``extension_unavailable``.

A toolkit is trusted code running inside the server. It reads its own
settings, named with its own prefix, from the environment; platform
credentials stay with the server, which hands the toolkit capabilities
instead (:class:`ModelServiceAccess`). Isolating the processes a toolkit
starts is the toolkit's job.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Protocol, runtime_checkable

from pydantic import BaseModel

from .models import Artifact, ToolkitOutput

__all__ = [
    "CANCEL",
    "ENTRY_POINT_GROUP",
    "PROTOCOL_VERSION",
    "SHUTDOWN",
    "ContextArtifact",
    "ModelServiceAccess",
    "PreflightContext",
    "Toolkit",
    "ToolkitContext",
    "ToolkitDescriptor",
    "ToolkitRequest",
    "protocol_compatible",
]

PROTOCOL_VERSION = "1.0"
"""Version of this protocol, ``major.minor``.

A server accepts a toolkit built for the same major version and a minor
version no newer than its own: a minor version only adds what a toolkit may
use, a major version changes what it must do.
"""

ENTRY_POINT_GROUP = "aion.toolkits"
"""Entry point group a toolkit registers its object under."""

CANCEL = "cancel"
"""Reason given to :meth:`Toolkit.cancel` when a caller cancelled the task."""

SHUTDOWN = "shutdown"
"""Reason given to :meth:`Toolkit.cancel` when the server is stopping."""


def protocol_compatible(toolkit_version: str, server_version: str = PROTOCOL_VERSION) -> bool:
    """Whether a server speaking ``server_version`` can host a toolkit built for ``toolkit_version``.

    Args:
        toolkit_version: The protocol version the toolkit declares.
        server_version: The protocol version of this server.

    Returns:
        True for the same major version and a minor version no newer than
        the server's; False otherwise, including for a malformed version.
    """
    try:
        toolkit_major, toolkit_minor = (int(part) for part in toolkit_version.split("."))
        server_major, server_minor = (int(part) for part in server_version.split("."))
    except ValueError:
        return False
    return toolkit_major == server_major and toolkit_minor <= server_minor


@dataclass(frozen=True)
class ToolkitDescriptor:
    """What a toolkit declares about itself.

    Attributes:
        uri: The A2A extension URI the toolkit serves. A request activating
            this extension is routed to the toolkit.
        description: One-line summary, shown on the agent card.
        requires: Extension URIs that must be active with this one (for
            example the daemon extension).
        payloads: Models of the event payloads the toolkit accepts in a
            message, each declaring ``SCHEMA_URI`` and ``EVENT_TYPE`` class
            variables. The server validates an incoming payload against them
            before the toolkit sees it.
        enabled_by_default: Whether the extension is active without being
            listed in the agent's ``enabled_extensions``.
        exclusive_per_context: Whether a context may hold only one unfinished
            task of this toolkit. A second request is refused with
            ``context_busy`` before a task is created.
        invocation_only: Whether, on a server configured with
            ``AION_CLIENT_ID``, only calls carrying a platform invocation
            token may start the toolkit. Other callers are refused with
            ``caller_not_allowed``. Without ``AION_CLIENT_ID`` the flag has
            no effect.
        protocol_version: The protocol version the toolkit is built for.
    """

    uri: str
    description: str
    requires: tuple[str, ...] = ()
    payloads: tuple[type[BaseModel], ...] = ()
    enabled_by_default: bool = False
    exclusive_per_context: bool = False
    invocation_only: bool = False
    protocol_version: str = PROTOCOL_VERSION


@dataclass(frozen=True)
class ToolkitRequest:
    """The incoming message, as the toolkit's payload models read it.

    Attributes:
        message_id: Id of the user message.
        text: The message's text parts, joined by newlines; empty when it
            has none.
        payload: The validated event payload the message carries, an
            instance of one of the descriptor's ``payloads``; None when the
            message carries no payload of this toolkit.
        event_id: Id of the event that carried the payload, if any.
        event_source: Source of that event, if any.
    """

    message_id: str
    text: str = ""
    payload: Optional[BaseModel] = None
    event_id: Optional[str] = None
    event_source: Optional[str] = None


@dataclass(frozen=True)
class ModelServiceAccess:
    """Calls to the Aion model service on the current caller's behalf.

    Captured while the request is current, so a run that outlives the request
    still attributes its usage to the caller who started it.

    Attributes:
        base_url: Base URL of the model service's OpenAI-compatible API.
        token: Returns a fresh short-lived bearer token; call it for every
            model call rather than keeping one.
        attribution: The header that attributes usage to the caller, as
            ``(name, value)``; None when usage is attributed to the
            deployment itself.
    """

    base_url: str
    token: Callable[[], Awaitable[str]]
    attribution: Optional[tuple[str, str]] = None


@dataclass(frozen=True)
class ContextArtifact:
    """The latest version of an artifact in the task's context.

    Attributes:
        artifact: The artifact as it was last written.
        task_id: The task that wrote this version.
        version: The version number, counted from 0 within the context.
        updated_at: When this version was written.
    """

    artifact: Artifact
    task_id: str
    version: int
    updated_at: Optional[datetime] = None


ArtifactFinder = Callable[[str], Awaitable[Optional[ContextArtifact]]]


async def _no_artifacts(_artifact_id: str) -> Optional[ContextArtifact]:
    return None


@dataclass(frozen=True)
class PreflightContext:
    """What the server knows about a request before its task exists.

    Attributes:
        context_id: The context the task would belong to.
        daemon_variables: Configuration variables of the request's daemon
            environment; empty without a daemon payload.
        model_service: Access to the model service on the caller's behalf;
            None when the server cannot call it for this request (no
            platform credentials, or no attribution usable for model calls).
    """

    context_id: str
    daemon_variables: Mapping[str, str] = field(default_factory=dict)
    model_service: Optional[ModelServiceAccess] = None


@dataclass(frozen=True)
class ToolkitContext:
    """What the server hands a toolkit for one turn of one task.

    Attributes:
        task_id: The task being served.
        context_id: The task's context.
        context_generation: Opaque marker of this use of the context id. It
            changes when a deleted context's id is taken again, so state a
            toolkit keeps outside the server (a branch, a file) can tell an
            old holder of the same id from the current one. None when the
            server does not track it.
        task_metadata: The toolkit's object in ``task.metadata`` as the last
            durable write left it; empty for a new task.
        daemon_variables: Configuration variables of the request's daemon
            environment; empty without a daemon payload.
        model_service: Access to the model service on the caller's behalf,
            or None (see :class:`PreflightContext`).
        secret_values: The deployment's secret values. The server masks them
            in everything the toolkit yields; a toolkit masks them in what it
            writes elsewhere (a commit, a pull request).
        artifact_finder: Looks up the latest version of an artifact in the
            context; use :meth:`find_artifact`.
    """

    task_id: str
    context_id: str
    context_generation: Optional[str] = None
    task_metadata: Mapping[str, Any] = field(default_factory=dict)
    daemon_variables: Mapping[str, str] = field(default_factory=dict)
    model_service: Optional[ModelServiceAccess] = None
    secret_values: tuple[str, ...] = ()
    artifact_finder: ArtifactFinder = _no_artifacts

    async def find_artifact(self, artifact_id: str) -> Optional[ContextArtifact]:
        """The latest version of an artifact in this context, or None.

        Only tasks the caller may see in this context are searched: the same
        holder that admitted the caller to the context (the user of a private
        context, the conversation of a shared one).

        Args:
            artifact_id: The artifact's ``id``.
        """
        return await self.artifact_finder(artifact_id)


@runtime_checkable
class Toolkit(Protocol):
    """The object a toolkit registers under :data:`ENTRY_POINT_GROUP`.

    One instance serves every task routed to the toolkit, concurrently.
    """

    descriptor: ToolkitDescriptor

    def configure(self) -> None:
        """Read and check the toolkit's settings from the environment.

        Called once at server startup. Raise to make the toolkit unavailable:
        the exception's message is logged as the reason and requests
        declaring the toolkit get ``extension_unavailable``.
        """
        ...

    async def availability(self, agent_config: Any) -> Optional[str]:
        """Whether the toolkit can work for this agent.

        Called once at startup, after :meth:`configure`.

        Args:
            agent_config: The agent's ``aion.core.config.AgentConfig``.

        Returns:
            None when available, otherwise the reason it is not.
        """
        ...

    async def preflight(self, request: ToolkitRequest, context: PreflightContext) -> None:
        """Refuse a request before its task is created.

        Checks only the request itself — its payload, its target, the
        caller's attribution — never the deployment (that is
        :meth:`configure`), and makes no network calls.

        Raises:
            aion.core.exceptions.ToolkitRejection: The request is refused;
                the caller gets a JSON-RPC error with the rejection's code
                and no task is created.
        """
        ...

    def start(
        self, request: ToolkitRequest, context: ToolkitContext
    ) -> AsyncIterator[ToolkitOutput]:
        """Run the first turn of a new task: events, then one result."""
        ...

    def resume(
        self, request: ToolkitRequest, context: ToolkitContext
    ) -> AsyncIterator[ToolkitOutput]:
        """Run the next turn of a task that waited for input."""
        ...

    async def cancel(self, task_id: str, reason: str) -> None:
        """Stop the turn running for ``task_id``.

        Idempotent and safe to call for a task that is not running. When the
        turn is being served by this process, the toolkit may still yield
        artifacts and a ``canceled`` result from its stream within the
        server's grace period; otherwise it only stops its work and reports
        to its log.

        Args:
            task_id: The task to stop.
            reason: :data:`CANCEL` or :data:`SHUTDOWN`.
        """
        ...

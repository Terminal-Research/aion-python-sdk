"""Finding the installed toolkits, registering their extensions, and keeping the ones that can serve.

A toolkit is a package that registers one ``aion.core.toolkit.Toolkit``
object under the ``aion.toolkits`` entry point group. At startup, before the
agent card is built, the server loads every such entry point:

1. The object must satisfy ``Toolkit`` and carry a ``ToolkitDescriptor``
   whose URI no other extension of this server claims.
2. Its extension is registered in ``aion_a2a_extension_registry``: the URI,
   description and ``requires``, a ``MessagesCollector`` over the
   descriptor's payload models that refuses a payload they reject with the
   descriptor's ``invalid_payload_code``, active by default only when the
   descriptor says so. The agent's ``enabled_extensions`` activates it like any other
   extension. Installing a toolkit does not expose it.
3. A toolkit whose extension is active for the agent must speak a compatible
   protocol version and declare usable payload models; then its
   ``configure()`` and ``availability()`` run, once.

Anything that goes wrong makes the toolkit unavailable and is logged as a
WARNING naming the entry point; when the toolkit's URI is known, the registry
records the reason, so a request declaring the extension is refused with the
code ``extension_unavailable`` and the card does not offer it. A broken
plugin never stops the server.
"""

from __future__ import annotations

import importlib.metadata
import logging
from collections.abc import Iterable
from typing import Any, Optional

from a2a.server.owner_resolver import OwnerResolver, resolve_user_scope
from pydantic import BaseModel

from aion.core.runtime.context.extensions import (
    ExtensionDescriptor,
    MarkerCollector,
    MessagesCollector,
    aion_a2a_extension_registry,
)
from aion.core.toolkit import (
    ENTRY_POINT_GROUP,
    PROTOCOL_VERSION,
    Toolkit,
    ToolkitDescriptor,
    protocol_compatible,
)
from aion.server.tasks.abandoned import AbandonedTasks
from aion.server.tasks.context_artifacts import ContextArtifacts
from aion.server.tasks.context_runs import ContextRuns

from .handler import ToolkitTaskHandler

__all__ = ["load_toolkit_handlers"]

logger = logging.getLogger(__name__)

_REGISTERED_URIS: set[str] = set()
"""The URIs this module registered, so a later discovery in the process may register them again."""


async def load_toolkit_handlers(
        agent_config: Any,
        *,
        entry_points: Optional[Iterable[importlib.metadata.EntryPoint]] = None,
        context_artifacts: Optional[ContextArtifacts] = None,
        context_runs: Optional[ContextRuns] = None,
        owner_resolver: OwnerResolver = resolve_user_scope,
        abandoned_tasks: Optional[AbandonedTasks] = None,
) -> list[ToolkitTaskHandler]:
    """Load every installed toolkit and return a handler for each one that can serve this agent.

    Args:
        agent_config: The agent's ``aion.core.config.AgentConfig``.
        entry_points: The entry points to load; every entry point of the
            ``aion.toolkits`` group by default.
        context_artifacts: The reader of artifact versions in a context that
            every handler numbers its toolkit's artifacts with and answers
            ``find_artifact`` from; see ``ToolkitTaskHandler``.
        context_runs: The reader of a context's generation and unfinished
            tasks that every handler hands its toolkit the generation from and
            keeps an ``exclusive_per_context`` toolkit to one task per context
            with; see ``ToolkitTaskHandler``.
        owner_resolver: The agent's owner resolver, which every handler reads
            the caller's context holder with.
        abandoned_tasks: What every handler settles an abandoned task with
            that would hold a context of an ``exclusive_per_context`` toolkit.

    Returns:
        One handler per toolkit that is enabled for the agent, configured and
        available, in entry point order.
    """
    if entry_points is None:
        entry_points = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
    enabled = frozenset(getattr(agent_config, "enabled_extensions", None) or ())
    handlers: list[ToolkitTaskHandler] = []
    claimed: set[str] = set()
    for entry_point in entry_points:
        handler = await _load(
            entry_point,
            agent_config,
            enabled,
            claimed,
            context_artifacts,
            context_runs,
            owner_resolver,
            abandoned_tasks,
        )
        if handler is not None:
            handlers.append(handler)
    return handlers


async def _load(
        entry_point: importlib.metadata.EntryPoint,
        agent_config: Any,
        enabled: frozenset[str],
        claimed: set[str],
        context_artifacts: Optional[ContextArtifacts],
        context_runs: Optional[ContextRuns],
        owner_resolver: OwnerResolver,
        abandoned_tasks: Optional[AbandonedTasks],
) -> Optional[ToolkitTaskHandler]:
    """One entry point: its handler, or None with the reason logged."""
    name = entry_point.name
    try:
        toolkit = entry_point.load()
    except Exception as error:  # noqa: BLE001 - a broken plugin must not stop the server
        logger.warning("Toolkit entry point '%s' could not be loaded: %s", name, _describe(error))
        return None

    descriptor = getattr(toolkit, "descriptor", None)
    if not isinstance(descriptor, ToolkitDescriptor) or not isinstance(toolkit, Toolkit):
        logger.warning(
            "Toolkit entry point '%s' names %r, which is not a toolkit: it needs a ToolkitDescriptor "
            "as `descriptor` and the methods of aion.core.toolkit.Toolkit",
            name,
            toolkit,
        )
        return None

    uri = descriptor.uri
    registered = {item.uri for item in aion_a2a_extension_registry.get_all()}
    if uri in claimed or (uri in registered and uri not in _REGISTERED_URIS):
        logger.warning(
            "Toolkit entry point '%s' claims the extension %s, which another extension of this "
            "server already has; it is not loaded",
            name,
            uri,
        )
        return None
    claimed.add(uri)

    problem = _incompatibility(descriptor)
    _register(descriptor, payloads_usable=problem is None, enabled=uri in enabled)
    if not (descriptor.enabled_by_default or uri in enabled):
        logger.info("Toolkit %s (entry point '%s') is installed and not enabled for this agent", uri, name)
        return None

    if problem is None:
        problem = await _setup_problem(toolkit, agent_config)
    if problem is not None:
        aion_a2a_extension_registry.mark_unavailable(uri, problem)
        logger.warning(
            "Toolkit %s (entry point '%s') is unavailable; requests declaring it are refused: %s",
            uri,
            name,
            problem,
        )
        return None

    logger.info("Toolkit %s (entry point '%s') serves this agent", uri, name)
    return ToolkitTaskHandler(
        toolkit,
        context_artifacts=context_artifacts,
        context_runs=context_runs,
        owner_resolver=owner_resolver,
        abandoned_tasks=abandoned_tasks,
    )


def _incompatibility(descriptor: ToolkitDescriptor) -> Optional[str]:
    """Why this server cannot host a toolkit described so, or None."""
    if not protocol_compatible(descriptor.protocol_version):
        return (
            f"the toolkit is built for toolkit protocol {descriptor.protocol_version!r}, "
            f"and this server speaks {PROTOCOL_VERSION}"
        )
    if not isinstance(descriptor.invalid_payload_code, str) or not descriptor.invalid_payload_code:
        return f"the descriptor's invalid_payload_code {descriptor.invalid_payload_code!r} is not a code"
    for payload in descriptor.payloads:
        if not (isinstance(payload, type) and issubclass(payload, BaseModel)):
            return f"the payload model {payload!r} is not a pydantic model"
        missing = [
            name for name in ("SCHEMA_URI", "EVENT_TYPE") if not isinstance(getattr(payload, name, None), str)
        ]
        if missing:
            return f"the payload model {payload.__name__} does not declare {', '.join(missing)}"
    return None


def _register(descriptor: ToolkitDescriptor, *, payloads_usable: bool, enabled: bool) -> None:
    """Register the toolkit's extension, and activate it when the agent enables it.

    The agent's ``enabled_extensions`` was applied to the registry before the
    toolkits were found, so a toolkit the agent enables is activated here.
    """
    collector = (
        MessagesCollector(*descriptor.payloads, invalid_payload_code=descriptor.invalid_payload_code)
        if descriptor.payloads and payloads_usable
        else MarkerCollector()
    )
    aion_a2a_extension_registry.register(
        ExtensionDescriptor(
            uri=descriptor.uri,
            collector=collector,
            requires=tuple(descriptor.requires),
            description=descriptor.description,
            active=descriptor.enabled_by_default,
        )
    )
    _REGISTERED_URIS.add(descriptor.uri)
    if enabled:
        aion_a2a_extension_registry.activate([descriptor.uri])


async def _setup_problem(toolkit: Toolkit, agent_config: Any) -> Optional[str]:
    """Run the toolkit's ``configure()`` and ``availability()``; why it cannot serve, or None."""
    try:
        toolkit.configure()
    except Exception as error:  # noqa: BLE001 - the toolkit's settings are its own
        return _describe(error)
    try:
        return await toolkit.availability(agent_config)
    except Exception as error:  # noqa: BLE001 - same
        return _describe(error)


def _describe(error: BaseException) -> str:
    """An exception as one line: its message, or its type when it has none."""
    return str(error) or type(error).__name__

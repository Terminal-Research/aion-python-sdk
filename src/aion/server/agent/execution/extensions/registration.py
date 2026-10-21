"""Registering the server's extensions, and creating the executors of the ones the agent enables.

Two steps of building the application (``AppFactory``):

1. ``register_extensions``, before the agent card exists: the registry is
   filled afresh with the protocol extensions of ``aion.core`` and the
   executable extensions this server ships (``EXECUTABLE_EXTENSIONS``), and
   activates every URI ``enabled_extensions`` lists. Options given to a URI
   that is not an executable extension of this server are a configuration
   error;
2. ``create_extension_executors``, when the application is built: each
   enabled executable extension's executor is created with its options. An
   executor that cannot be created - options it refuses, something it needs
   missing on this deployment - is a configuration error, and the server does
   not start.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from aion.core.exceptions import ConfigurationError
from aion.core.runtime.context.extensions import (
    AionA2AExtensionRegistry,
    aion_a2a_extension_registry,
    register_protocol_extensions,
)

from .evolution import EVOLUTION_EXTENSION
from .executable import ExecutableExtensionDescriptor, ExtensionExecutor

__all__ = [
    "EXECUTABLE_EXTENSIONS",
    "EnabledExtension",
    "create_extension_executors",
    "register_extensions",
]

logger = logging.getLogger(__name__)

EXECUTABLE_EXTENSIONS: tuple[ExecutableExtensionDescriptor, ...] = (EVOLUTION_EXTENSION,)
"""The executable extensions this server ships."""


@dataclass(frozen=True)
class EnabledExtension:
    """An executable extension the agent enables.

    Attributes:
        descriptor: Its registration.
        options: The options ``aion.yaml`` gives it, as written; empty for a
            bare URI.
    """

    descriptor: ExecutableExtensionDescriptor
    options: Mapping[str, Any] = field(default_factory=dict)


def register_extensions(
        agent_config: Any,
        *,
        registry: AionA2AExtensionRegistry = aion_a2a_extension_registry,
        executables: Iterable[ExecutableExtensionDescriptor] = EXECUTABLE_EXTENSIONS,
) -> list[EnabledExtension]:
    """Register every extension and activate the ones the agent enables.

    Args:
        agent_config: The agent's ``aion.core.config.AgentConfig``.
        registry: The registry to fill; emptied first.
        executables: The executable extensions to register.

    Returns:
        The executable extensions the agent enables, in ``executables``
        order, with their options.

    Raises:
        ConfigurationError: ``enabled_extensions`` gives options to a URI
            that is not an executable extension of this server.
    """
    executables = tuple(executables)
    enabled = list(getattr(agent_config, "enabled_extension_uris", None) or ())
    options: Mapping[str, Mapping[str, Any]] = getattr(agent_config, "extension_options", None) or {}

    registry.clear()
    register_protocol_extensions(registry)
    executable_uris = {descriptor.uri for descriptor in executables}
    for uri, given in options.items():
        if given and uri not in executable_uris:
            raise ConfigurationError(
                f"enabled_extensions gives options to {uri}, which takes none: {', '.join(sorted(given))}"
            )

    enabled_executables: list[EnabledExtension] = []
    for descriptor in executables:
        registry.register(descriptor)
        if descriptor.active or descriptor.uri in enabled:
            enabled_executables.append(EnabledExtension(descriptor, dict(options.get(descriptor.uri) or {})))
        else:
            logger.info("Executable extension %s is not enabled for this agent", descriptor.uri)

    registry.activate(enabled)
    return enabled_executables


def create_extension_executors(enabled: Iterable[EnabledExtension]) -> dict[str, ExtensionExecutor]:
    """Create the executor of every enabled executable extension.

    Args:
        enabled: What ``register_extensions`` returned.

    Returns:
        The executors by URI, in ``enabled`` order.

    Raises:
        ConfigurationError: An executor could not be created; the message
            names the extension and why.
    """
    executors: dict[str, ExtensionExecutor] = {}
    for extension in enabled:
        uri = extension.descriptor.uri
        try:
            executors[uri] = extension.descriptor.executor(extension.options)
        except ConfigurationError:
            raise
        except Exception as error:
            raise ConfigurationError(
                f"the extension {uri} cannot run on this server: {error or type(error).__name__}"
            ) from error
        logger.info("Executable extension %s serves this agent", uri)
    return executors

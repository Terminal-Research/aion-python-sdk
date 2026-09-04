"""Lazy exports for ``aion.cli.services``.

This package contains several service trees with optional sibling-package
dependencies. Importing them eagerly makes lightweight CLI paths, such as
``aion chat``, pull in unrelated GraphQL and server dependencies during module
import. The namespace now resolves exported symbols lazily so subpackages can be
used independently.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


__all__ = [
    "AionDeploymentRegisterVersionService",
    "EnvironmentContext",
    "ServeAgentStartupService",
    "ServeEnvironmentPreparerService",
    "ServeMonitoringService",
    "ServeProxyStartupService",
    "ServeShutdownService",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "AionDeploymentRegisterVersionService": (
        "aion.cli.services.aion",
        "AionDeploymentRegisterVersionService",
    ),
    "EnvironmentContext": (
        "aion.cli.services.serve",
        "EnvironmentContext",
    ),
    "ServeAgentStartupService": (
        "aion.cli.services.serve",
        "ServeAgentStartupService",
    ),
    "ServeEnvironmentPreparerService": (
        "aion.cli.services.serve",
        "ServeEnvironmentPreparerService",
    ),
    "ServeMonitoringService": (
        "aion.cli.services.serve",
        "ServeMonitoringService",
    ),
    "ServeProxyStartupService": (
        "aion.cli.services.serve",
        "ServeProxyStartupService",
    ),
    "ServeShutdownService": (
        "aion.cli.services.serve",
        "ServeShutdownService",
    ),
}


def __getattr__(name: str) -> Any:
    """Resolve exported service symbols lazily on first access."""
    try:
        module_name, attribute_name = _LAZY_IMPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    module = import_module(module_name)
    value = getattr(module, attribute_name)
    globals()[name] = value
    return value

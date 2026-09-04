"""Agent adapter exception hierarchy for the Aion server."""

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aion.server.plugins.registry import SkippedPlugin


class AdapterError(Exception):
    """Base class for all agent adapter errors."""


class AdapterNotFoundError(AdapterError):
    """Raised when no adapter is registered for the requested framework or agent type."""

    def __init__(self, framework_name: str | None = None, agent_type: str | None = None):
        if framework_name:
            message = f"No adapter registered for framework '{framework_name}'"
        elif agent_type:
            message = f"No adapter can handle agent of type '{agent_type}'"
        else:
            message = "No suitable adapter found"
        super().__init__(message)


class NoAdapterFoundError(AdapterError, ValueError):
    """Raised when no registered adapter could build a particular agent.

    Also a ValueError, because that is what this used to raise and what
    callers still catch.

    The interesting case is an agent written for a framework whose plugin was
    never loaded: the adapter list is short for a reason, and the reason is one
    `pip install` away. Plugins skipped during discovery are named here, with
    the extra that would bring them back.
    """

    def __init__(
        self,
        agent_id: str,
        module_path: str,
        available_frameworks: Sequence[str],
        errors: Sequence[str] = (),
        skipped_plugins: "Sequence[SkippedPlugin]" = (),
    ):
        lines = [
            f"No adapter found for agent '{agent_id}' in module '{module_path}'.",
            f"Available frameworks: {list(available_frameworks)}",
        ]
        if errors:
            lines.append("Errors encountered:")
            lines.extend(f"  - {error}" for error in errors)
        if skipped_plugins:
            lines.append("Frameworks whose plugins were not loaded:")
            lines.extend(f"  - {plugin.describe()}" for plugin in skipped_plugins)
        super().__init__("\n".join(lines))


class AdapterRegistrationError(AdapterError):
    """Raised when an adapter cannot be registered (e.g., duplicate framework name)."""


class ExecutionError(AdapterError):
    """Raised when agent execution fails unexpectedly."""


class StateRetrievalError(AdapterError):
    """Raised when fetching or parsing the agent execution state fails."""


class MessageConversionError(AdapterError):
    """Raised when converting messages between A2A and framework formats fails."""


class ConfigurationError(AdapterError):
    """Raised when the agent configuration is invalid or missing required fields."""


class UnsupportedOperationError(AdapterError):
    """Raised when an operation is not supported by the current adapter."""

    def __init__(self, operation: str, framework: str):
        message = f"Operation '{operation}' is not supported by '{framework}' adapter"
        super().__init__(message)



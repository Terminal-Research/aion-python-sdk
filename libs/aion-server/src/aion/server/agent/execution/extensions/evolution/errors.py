"""Handler-level errors for the evolution extension.

Kept toolkit-free so handler.py can catch them without importing the
optional toolkit: DirectiveError is raised by directive.py (pure aion-core
parsing), ExtensionSetupError by tools_factory.py (behind the lazy import
boundary). Both terminate the routed task with a FAILED status carrying the
message.

Named `ExtensionSetupError` — not `SetupError` — because the toolkit itself
defines a `SetupError` with an unrelated meaning (its test-environment
preparation failed); a shared bare name across the two packages would be a
trap for whoever reads a traceback out of context.
"""

from __future__ import annotations

__all__ = ["EvolutionHandlerError", "DirectiveError", "ExtensionSetupError"]


class EvolutionHandlerError(Exception):
    """A routed evolution task cannot start; the message is user-facing."""


class DirectiveError(EvolutionHandlerError):
    """The routed request does not carry a usable evolution directive."""


class ExtensionSetupError(EvolutionHandlerError):
    """The evolution extension is enabled but its environment is not usable."""

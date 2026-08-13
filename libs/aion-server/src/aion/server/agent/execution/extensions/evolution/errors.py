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

Each error carries two things the handler needs to keep apart:

- `code` — a stable machine-readable token that rides the FAILED status's
  progress struct, so the caller can branch on *why* without parsing prose.
  Adding a code is a contract change; changing an existing one breaks callers.
- `client_text` — what the caller is actually told. It defaults to the
  exception message and is narrowed only where that message describes the
  deployment rather than the request: the improver's environment (which env
  vars are missing, which binary is absent) is the operator's business, and
  the caller is a client on the other side of an A2A boundary. The full
  message always reaches the logs, which is where whoever owns the
  deployment is looking.

The split is drawn by *whose fault it is*, not by severity. A caller who sent
a directive this deployment cannot serve gets the detail, because only that
detail tells them what to send instead.
"""

from __future__ import annotations

__all__ = [
    "EvolutionHandlerError",
    "DirectiveError",
    "ExtensionSetupError",
    "UnsupportedDirectiveError",
    "INTERNAL_ERROR_CODE",
    "INTERNAL_ERROR_TEXT",
]

# The crash path has no exception class of its own: anything escaping the
# worker's stream is a wiring bug, and its text is a traceback fragment that
# says nothing to a caller.
INTERNAL_ERROR_CODE = "internal_error"
INTERNAL_ERROR_TEXT = "The evolution could not be started because of an internal error."


class EvolutionHandlerError(Exception):
    """A routed evolution task cannot start.

    `str(self)` is the operator-facing detail (logged); `client_text` is what
    crosses the A2A boundary. They are the same unless a subclass narrows it.
    """

    code = "evolution_error"

    @property
    def client_text(self) -> str:
        return str(self)


class DirectiveError(EvolutionHandlerError):
    """The routed request does not carry a usable evolution directive.

    Detail is kept for the caller: they authored the request, and naming the
    missing or malformed part is the only way they can fix it.
    """

    code = "invalid_directive"


class ExtensionSetupError(EvolutionHandlerError):
    """The evolution extension is enabled but its environment is not usable.

    Narrowed on the way out: the message names deployment internals (env var
    names, binaries, endpoints) that a caller can neither act on nor should
    learn from an error string.
    """

    code = "misconfigured_deployment"

    @property
    def client_text(self) -> str:
        return (
            "This deployment is not configured to run evolutions. "
            "The improver's operator has the details."
        )


class UnsupportedDirectiveError(ExtensionSetupError):
    """The directive asks for something the installed toolkit cannot do.

    A subclass of ExtensionSetupError because the deployment is indeed what
    falls short — but the caller is the only one who can route around it, by
    sending a directive this deployment supports, so the detail (which field,
    which value, what is accepted) travels to them intact.
    """

    code = "unsupported_directive"

    @property
    def client_text(self) -> str:
        return str(self)

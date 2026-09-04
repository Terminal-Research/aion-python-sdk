"""Handler-level errors for the evolution extension.

Kept toolkit-free so handler.py can catch them without importing the
optional toolkit: DirectiveError is raised by directive.py (pure aion.core
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
  exception message and is narrowed only where a raise site cannot guarantee
  its message is free of host/deployment detail (see UnsupportedDirectiveError
  vs ExtensionSetupError below) — not by whose fault the failure is. The full
  message (via `from ex` chaining) always reaches the logs too.

The caller on the other side of the A2A boundary is this deployment's own
trusted control plane — the same party that operates the deployment — so
there is no confidentiality boundary to defend by default; the bar for
narrowing a message is "can this leak something host-specific", not "is this
the deployment's fault".
"""

from __future__ import annotations

__all__ = [
    "EvolutionHandlerError",
    "DirectiveError",
    "ExtensionSetupError",
    "UnsupportedDirectiveError",
    "INTERNAL_ERROR_CODE",
]

# The crash path has no exception class of its own: anything escaping the
# worker's stream is a wiring bug, and its text is a traceback fragment that
# says nothing to a caller. The message the caller does get is built from the
# run's accumulated progress instead — see `events.internal_error_text`.
INTERNAL_ERROR_CODE = "internal_error"


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

    Not narrowed: every raise site in tools_factory.py/provider.py/handler.py
    builds its message from known, non-secret parts (env var names, accepted
    values, a missing module's `.name`) — never a bare `str(exception)` from a
    third party that could carry a filesystem path or similar host detail. As
    long as that invariant holds, the message is safe to hand to the caller,
    and doing so is what lets a FAILED task actually say why: the caller is
    the trusted control plane calling its own deployment, i.e. the operator
    with the full picture, not an untrusted third party being told to go ask
    someone else. Secrets (tokens, keys) never appear in these messages by
    construction — only their *names* and whether they're set.
    """

    code = "misconfigured_deployment"


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

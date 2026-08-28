"""ExtensionPreflightError, shared by the handler protocol and its implementations.

Lives in its own leaf module for the same reason as availability.py: base.py
imports the concrete handler subpackages (e.g. evolution) for discovery, and
those handlers raise this type - a shared leaf module keeps the import graph
acyclic.
"""

from __future__ import annotations

__all__ = ["ExtensionPreflightError"]


class ExtensionPreflightError(Exception):
    """A specific request cannot be served, discovered before its task exists.

    Raised from `ExtensionTaskHandler.preflight()`, which the executor calls
    once a request has been routed to an extension handler but before the
    task is persisted/enqueued (see request_executor._resolve) - so raising
    here rejects the request at the JSON-RPC level (no FAILED task left
    behind), same as ExtensionActivationError one step earlier in the same
    pipeline, but for handler-owned checks (e.g. deployment env config)
    rather than aion-core's schema/co-activation checks.

    Unlike `availability()` - checked once at executor startup and cached for
    the process's lifetime - `preflight()` runs fresh on every request, so it
    is the right place for anything that can change on a live pod without a
    restart (env vars a deployment reconfigures in place).
    """

"""Identity of the process a log line was written by."""

from __future__ import annotations

from typing import Optional

__all__ = ["set_process_role", "get_process_role"]

_process_role: Optional[str] = None


def set_process_role(role: str) -> None:
    """Name the process that is writing to the log.

    ``aion serve`` runs the CLI, the proxy and every agent as separate processes
    sharing one console, and their output interleaves. An agent names itself, but
    the CLI and the proxy have no agent to name, so without this their lines are
    indistinguishable from each other.

    Call once per process, as early as the process has an identity. Children must
    set their own: the default start method re-imports this module rather than
    inheriting the parent's value.

    Args:
        role: Short label for the process, e.g. "CLI" or "Proxy".
    """
    global _process_role
    _process_role = role


def get_process_role() -> Optional[str]:
    """Return this process's role, or None if it never claimed one."""
    return _process_role

"""How an agent process spends its shutdown budget, ``AION_SHUTDOWN_TIMEOUT``.

``aion serve`` gives every agent process the budget after SIGINT or SIGTERM
and kills what is still running once it is spent. The agent process divides
it among the steps of its shutdown, which run one after another:

1. **toolkit turns** - every toolkit turn running in the process is asked to
   stop (``Toolkit.cancel`` with ``within`` set to this window) and the turns
   are awaited together for at most this long;
2. **graceful** - uvicorn's ``timeout_graceful_shutdown``: the wait for the
   connections still open, after which they are cancelled;
3. **settlement** - the drain of the active tasks settles the tasks the
   shutdown interrupted;
4. **cleanup** - plugins, the database and the process exit.

The last three are fixed in size and shrink with a small budget: each takes
at most a share of it (``GRACEFUL_SHARE``, ``SETTLEMENT_SHARE``,
``CLEANUP_SHARE``). The toolkits' turns get the rest, at least
``MIN_TOOLKIT_TURNS_SECONDS``, because that is where a toolkit saves its
work. The default budget of 25 s is 15 + 5 + 2 + 3.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from aion.server.settings import app_settings

__all__ = [
    "CLEANUP_SECONDS",
    "CLEANUP_SHARE",
    "GRACEFUL_SECONDS",
    "GRACEFUL_SHARE",
    "MIN_SANE_SHUTDOWN_SECONDS",
    "MIN_TOOLKIT_TURNS_SECONDS",
    "SETTLEMENT_SECONDS",
    "SETTLEMENT_SHARE",
    "ShutdownBudget",
    "shutdown_budget",
]

GRACEFUL_SECONDS = 5.0
"""The longest uvicorn waits for open connections once the toolkits' turns were stopped."""

GRACEFUL_SHARE = 0.2
"""The largest share of the budget uvicorn's wait takes."""

SETTLEMENT_SECONDS = 2.0
"""The longest the drain spends settling the tasks the shutdown interrupted."""

SETTLEMENT_SHARE = 0.08
"""The largest share of the budget the settlement takes."""

CLEANUP_SECONDS = 3.0
"""Room for the rest of an agent's shutdown: plugins, the database, the process exit."""

CLEANUP_SHARE = 0.12
"""The largest share of the budget the cleanup is given."""

MIN_TOOLKIT_TURNS_SECONDS = 1.0
"""The shortest window the toolkits' turns get, whatever the budget."""

MIN_SANE_SHUTDOWN_SECONDS = 10.0
"""A budget below this is too short for a toolkit to save its work; the agent warns about it at startup."""


@dataclass(frozen=True)
class ShutdownBudget:
    """An agent process's shutdown budget, divided among its steps.

    Attributes:
        total: The whole budget, ``AION_SHUTDOWN_TIMEOUT``: what ``aion
            serve`` gives the process before it kills it.
        toolkit_turns: The window the toolkits' running turns get to stop,
            passed to ``Toolkit.cancel`` as ``within``.
        graceful: uvicorn's ``timeout_graceful_shutdown``.
        settlement: The longest the settlement of interrupted tasks may take.
        cleanup: Room left for the rest of the shutdown.
    """

    total: float
    toolkit_turns: float
    graceful: float
    settlement: float
    cleanup: float

    @classmethod
    def of(cls, total: float) -> "ShutdownBudget":
        """Divide ``total`` seconds among the steps of a shutdown.

        Below about 1.7 s the steps add up to more than ``total``: the
        toolkits' turns keep their minimum, and ``aion serve`` kills the
        process when ``total`` is spent.
        """
        graceful = min(GRACEFUL_SECONDS, GRACEFUL_SHARE * total)
        settlement = min(SETTLEMENT_SECONDS, SETTLEMENT_SHARE * total)
        cleanup = min(CLEANUP_SECONDS, CLEANUP_SHARE * total)
        toolkit_turns = max(MIN_TOOLKIT_TURNS_SECONDS, total - graceful - settlement - cleanup)
        return cls(
            total=total,
            toolkit_turns=toolkit_turns,
            graceful=graceful,
            settlement=settlement,
            cleanup=cleanup,
        )

    @property
    def too_short(self) -> bool:
        """Whether the budget is below ``MIN_SANE_SHUTDOWN_SECONDS``."""
        return self.total < MIN_SANE_SHUTDOWN_SECONDS


def shutdown_budget(total: Optional[float] = None) -> ShutdownBudget:
    """The shutdown budget of this process, from ``AION_SHUTDOWN_TIMEOUT`` unless ``total`` is given."""
    return ShutdownBudget.of(app_settings.shutdown_timeout if total is None else total)

"""Task ownership: one instance at a time may execute a given task.

The right to execute a task is a row in a shared table with an expiry. While
its holder keeps extending it, that holder executes; once it stops, another
instance may take the row over. Three facts that are otherwise conflated into
"the task is in my process's memory" are kept apart here:

* what the task is doing now — the ``tasks`` row;
* who may move it — the ``task_claims`` row;
* who is actually running it — an ``ActiveTask`` in one process.

The third follows from the second and never the other way around: a live object
in memory proves nothing about ownership.

Nothing outside this package needs to know whether the process is backed by
PostgreSQL or by the single-process development implementation.
"""

from .config import (
    RECONCILER_ENV_VAR,
    SHUTDOWN_DB_TIMEOUT_SECONDS,
    LeaseSettings,
)
from .heartbeat import OwnershipHeartbeat
from .port import DegenerateOwnershipProvider, OwnershipProvider
from .postgres import PostgresOwnershipProvider
from .types import (
    Busy,
    Claim,
    Lost,
    Owned,
    OwnershipLossCallback,
    TaskOwnershipBusy,
    TaskOwnershipLost,
    Unknown,
)

__all__ = [
    # Timing and switches
    "SHUTDOWN_DB_TIMEOUT_SECONDS",
    "RECONCILER_ENV_VAR",
    "LeaseSettings",
    # Vocabulary
    "Claim",
    "Busy",
    "Owned",
    "Lost",
    "Unknown",
    "OwnershipLossCallback",
    "TaskOwnershipBusy",
    "TaskOwnershipLost",
    # Port and implementations
    "OwnershipProvider",
    "DegenerateOwnershipProvider",
    "PostgresOwnershipProvider",
    "OwnershipHeartbeat",
]

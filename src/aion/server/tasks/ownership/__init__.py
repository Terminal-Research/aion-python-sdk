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

Relation to a2a cluster mode
----------------------------

a2a-sdk has its own multi-replica mode, which this package does not use.
It is described here as of a2a-sdk 1.2.2 and of a2a-go at commit 534a60f.
a2a-python ports its cluster design from a2a-go, so a2a-go is the best
forecast of where a2a-python is heading. Re-check this section on every
a2a-sdk bump.

a2a-python 1.2 (``a2a.server.cluster``):

* ``VersionedTaskStore`` saves with optimistic concurrency against a
  ``TaskVersion`` kept in its own ``task_versions`` table. In the same
  transaction it appends the event to ``task_events``.
* ``TaskEventStream`` lets a replica that is not running the agent follow a
  task. ``DatabaseTaskEventStream`` polls ``task_events``.
* ``DefaultRequestHandlerV2._cancel_remote`` writes ``CANCELED`` over the
  stored version. The executing replica notices only when its next save
  fails the version check, so ``AgentExecutor.cancel`` never runs there.
* There is no lease, no heartbeat and no recovery of a dead replica's tasks.
  The agent runs on whichever replica received the request.

a2a-go (``a2asrv/workqueue``, ``internal/taskexec``) adds:

* a work queue whose payloads are ``execute`` or ``cancel``, consumed by any
  node;
* an optional ``LeaseManager``/``Lease`` and a ``Heartbeater``;
* a ``ContextCodec`` that serialises the call context into the payload.

Even there, cancellation reaches the running execution only through the
optimistic-concurrency conflict on its next write. ``AgentExecutor.Cancel``
runs on whichever node picked up the cancel payload. A crashed node's work is
redelivered and executed again from the start with the stored task. Nothing
marks a task failed after repeated attempts.

How the pieces map:

====================================  ================================  ==================================
This package and its callers          a2a-python 1.2                    a2a-go
====================================  ================================  ==================================
``OwnershipProvider.acquire`` /       none                              ``LeaseManager.Acquire`` /
``release``, ``Claim``                                                  ``Lease.Release``
``Busy`` → ``TaskOwnershipBusy``      none                              ``ErrLeaseAlreadyTaken``
``OwnershipHeartbeat``                none                              ``Heartbeater``
``ClaimReaper`` settles FAILED        none                              redelivery, ``Execute`` again
fenced write by ``owner_token``       ``VersionedTaskStore`` CAS        ``taskstore.Store`` OCC
cancel via ``ControlSignal.CANCEL``   ``_cancel_remote`` writes         ``cancel`` payload + OCC
on the claim, run by the owner        ``CANCELED``
``TaskEventListener`` NOTIFY          ``DatabaseTaskEventStream`` poll  ``eventqueue`` ``Puller`` poll
attach elsewhere →                    ``_subscribe_remote`` snapshot    ``remoteSubscription``
``TaskOwnershipBusy``                 + live tail
call context kept in-process          none (executes locally)           ``ContextCodec`` in ``Payload``
====================================  ================================  ==================================

Where the design differs on purpose:

* The owner runs ``ActiveTask.cancel`` and therefore ``AgentExecutor.cancel``.
  That is where an evolution run pushes its rescue branch, and neither
  upstream design calls it on the executing process.
* A task whose owner died is settled as FAILED rather than executed again.
  An agent run has side effects (commits, pushes, tool calls) that a second
  run from the start would repeat.
* Exclusivity is enforced before execution starts, by the claim. A version
  check only detects the second execution once it tries to write.
* Writes are fenced by the claim token, not by a version, so the ``tasks``
  table carries no version column.

Moving onto upstream later goes through the a2a extension points. None of
these steps changes the ``tasks`` table:

1. Wrap ``PostgresTaskStore`` in a ``VersionedTaskStore``. Keep the version
   in a separate table, the way a2a keeps ``task_versions``. Keep the claim
   fence: the version only orders writes, while the claim decides who may
   write.
2. Add an event journal written in the same transaction as the fenced
   write, and a ``TaskEventStream`` woken by NOTIFY on
   ``TASK_EVENT_CHANNEL``. ``AionActiveTaskRegistry.get_for_attach`` can then
   answer a remote attach with a snapshot and a tail instead of
   ``TaskOwnershipBusy``.
3. If a2a-python gains leases, implement its lease and heartbeat interfaces
   on ``task_claims`` through this package's provider. Keep the
   ``_cancel_local``/``_cancel_remote`` overrides in ``AionRequestHandler``
   until upstream cancellation reaches the owner's executor.
4. If a2a-python gains a work queue, ``execute`` stays on the claiming
   process. A queue payload would need the call context serialised the way
   a2a-go's ``ContextCodec`` does it, and the agent would need to tolerate
   a re-run. Both are product decisions, not mechanical ports.
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
    ControlSignal,
    ControlSignalCallback,
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
    "ControlSignal",
    "ControlSignalCallback",
    "TaskOwnershipBusy",
    "TaskOwnershipLost",
    # Port and implementations
    "OwnershipProvider",
    "DegenerateOwnershipProvider",
    "PostgresOwnershipProvider",
    "OwnershipHeartbeat",
]

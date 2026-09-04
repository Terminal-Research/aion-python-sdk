"""Timing and switches for task ownership leases.

Every number here is a decision about how long a lost owner may hold work
hostage, balanced against how much load renewal puts on the database. They are
gathered in one settings object so that a test can shorten them all without
each collaborator growing its own knob.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

__all__ = [
    # Genuinely used outside this module; everything else here is an
    # implementation detail of LeaseSettings' defaults.
    "SHUTDOWN_DB_TIMEOUT_SECONDS",
    "RECONCILER_ENV_VAR",
    "RECONCILE_ADVISORY_LOCK_KEY",
    "LeaseSettings",
    "reaper_enabled_by_environment",
]

# How long a lease outlives its last renewal: the worst pause the heartbeat
# itself may take. There are no blocking sections in the executors.
LEASE_TTL_SECONDS = 60.0

# A quarter of the TTL, so three consecutive missed renewals are forgiven.
HEARTBEAT_INTERVAL_SECONDS = 15.0

# One interval: work is allowed until the confirmed lease end minus this.
HEARTBEAT_SAFETY_MARGIN_SECONDS = 15.0

# A blink of the network must not cost a third of the TTL.
UNKNOWN_RETRY_SECONDS = 2.0

# Half the TTL, so the worst "a dead task still looks alive" is TTL plus half
# a pass.
RECONCILE_INTERVAL_SECONDS = 30.0

# The claim-expiry pass is the everyday path: leases expire constantly, and
# reacting within one RECONCILE_INTERVAL_SECONDS bounds how long a genuinely
# dead task looks alive. The active-task sweep exists for a case that should
# not occur at all - a task presented as running with no lease behind it,
# meaning something already violated the claim/task invariant elsewhere in
# the codebase - so checking for it five times less often trades a longer
# window on an already-rare failure for less redundant scanning per pod.
ACTIVE_TASK_SWEEP_INTERVAL_SECONDS = RECONCILE_INTERVAL_SECONDS * 5

# How many candidates one pass examines, not how many rows it locks at once.
RECONCILE_BATCH_SIZE = 50

# Distinct from MIGRATION_ADVISORY_LOCK_KEY (aion.db.postgres.migrations.env):
# both are session-scoped Postgres advisory locks, and two subsystems sharing
# one key would each mistake the other's lock for their own. Non-blocking
# (pg_try_advisory_lock) at the call site, not pg_advisory_lock: a pod that
# loses the race skips this tick entirely rather than queuing behind whichever
# pod is already reaping - every pod already retries next tick regardless.
RECONCILE_ADVISORY_LOCK_KEY = 7_382_194_711

# Ten times the TTL. Applied only to tasks with no lease at all, where there is
# no evidence beyond "it has not changed in a long time".
ORPHAN_TASK_AGE_SECONDS = 10 * LEASE_TTL_SECONDS

# Shutdown is not delayed for an unreachable database: Kubernetes will end the
# process on its own timeout anyway, and the reaper finishes the story.
SHUTDOWN_DB_TIMEOUT_SECONDS = 2.0

# A claim statement that neither answers nor fails is the one failure the lease
# cannot survive: the fail-closed deadline is only evaluated between attempts,
# so an unbounded await parks the supervisor while every lease in the process
# quietly expires. Bounding the statement converts that silence into an unknown
# outcome, which the deadline logic already handles.
DB_STATEMENT_TIMEOUT_SECONDS = 10.0

# How long a signaled cancellation may go un-honored, at a live lease, before
# the reaper forces the task closed regardless. Strictly greater than the
# evolution handler's own cancel-drain budget (60s -
# aion.server.agent.execution.extensions.evolution.handler._CANCEL_DRAIN_TIMEOUT_S)
# so the reaper never preempts an owner still mid-rescue.
CANCEL_GRACE_SECONDS = 120.0

# How long the pod that received tasks/cancel waits, after marking the claim,
# before answering with whatever the store currently holds instead of the
# settled terminal task. Short: this blocks an HTTP request, and a client that
# times out here still gets the terminal task later via push or resubscribe.
CANCEL_WAIT_SECONDS = 10.0

# Reclaiming expired leases is only safe once every writer renews them. The
# heartbeat is now assumed present on every deployed instance, so the reaper
# defaults on; set this to a falsy value to disable it, e.g. during a rollout
# that still has instances without the heartbeat.
RECONCILER_ENV_VAR = "TASK_OWNERSHIP_REAPER"


@dataclass(frozen=True, slots=True)
class LeaseSettings:
    """The timing of one process's leases."""

    ttl_seconds: float = LEASE_TTL_SECONDS
    heartbeat_interval_seconds: float = HEARTBEAT_INTERVAL_SECONDS
    safety_margin_seconds: float = HEARTBEAT_SAFETY_MARGIN_SECONDS
    unknown_retry_seconds: float = UNKNOWN_RETRY_SECONDS
    statement_timeout_seconds: float = DB_STATEMENT_TIMEOUT_SECONDS
    reconcile_interval_seconds: float = RECONCILE_INTERVAL_SECONDS
    active_task_sweep_interval_seconds: float = ACTIVE_TASK_SWEEP_INTERVAL_SECONDS
    reconcile_batch_size: int = RECONCILE_BATCH_SIZE
    orphan_task_age_seconds: float = ORPHAN_TASK_AGE_SECONDS
    cancel_grace_seconds: float = CANCEL_GRACE_SECONDS
    cancel_wait_seconds: float = CANCEL_WAIT_SECONDS

    @property
    def working_window_seconds(self) -> float:
        """How long work may continue on a lease that was just confirmed."""
        return max(0.0, self.ttl_seconds - self.safety_margin_seconds)


def reaper_enabled_by_environment() -> bool:
    """Read the reaper switch, defaulting to on."""
    value = os.getenv(RECONCILER_ENV_VAR)
    if value is None:
        return True
    return value.strip().lower() not in {"0", "false", "no", "off"}

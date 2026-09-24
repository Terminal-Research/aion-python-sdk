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
    "LEASE_TTL_SECONDS",
    "MIN_LEASE_TTL_SECONDS",
    "MIN_RENEWAL_RESERVE_SECONDS",
    "UNKNOWN_RETRY_JITTER",
    "LeaseSettings",
    "lease_settings_from_environment",
    "reaper_enabled_by_environment",
]

# How long a lease outlives its last renewal: the worst pause the heartbeat
# itself may take. There are no blocking sections in the executors. This is
# the default of TASK_OWNERSHIP_LEASE_TTL_SECONDS; every lease timing below
# except the cancellation ones is a fixed share of it (LeaseSettings.for_ttl).
LEASE_TTL_SECONDS = 60.0

# How much time must be left over when a renewal hangs for its whole
# statement timeout and its one retry hangs too: the heartbeat's own
# scheduling, and the teardown of a claim it gives up on, happen in it.
MIN_RENEWAL_RESERVE_SECONDS = 1.0

# The smallest TTL a deployment may configure. At 24s the scaled timings give
# a 4s statement timeout and leave 1.5s of reserve (see
# LeaseSettings.renewal_reserve_seconds); much below it the fixed retry delay
# eats the working window. It is also the smallest TTL the distributed
# scenarios can run at: a cancel there waits CANCEL_WAIT_SECONDS for an owner
# whose lease must still be alive afterwards.
MIN_LEASE_TTL_SECONDS = 24.0

# A quarter of the TTL, so three consecutive missed renewals are forgiven.
HEARTBEAT_INTERVAL_SECONDS = 15.0

# One interval: work is allowed until the confirmed lease end minus this.
HEARTBEAT_SAFETY_MARGIN_SECONDS = 15.0

# A blink of the network must not cost a third of the TTL.
UNKNOWN_RETRY_SECONDS = 2.0

# Each retry waits UNKNOWN_RETRY_SECONDS give or take this share, so that
# instances which lost the database together do not return to it together.
UNKNOWN_RETRY_JITTER = 0.25

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
# outcome, which the deadline logic already handles. A sixth of the default
# TTL; LeaseSettings.for_ttl keeps that share below it.
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

    @classmethod
    def for_ttl(cls, ttl_seconds: float) -> LeaseSettings:
        """Lease timing scaled to one TTL, in the deployed proportions.

        The heartbeat, the safety margin, the reconcile passes and the orphan
        age keep the shares of the TTL they have at the default, so
        ``for_ttl(LEASE_TTL_SECONDS)`` is exactly ``LeaseSettings()``. The
        statement timeout is a sixth of the TTL, capped at the default: a
        renewal that starts on its tick and hangs, then a retry after the
        longest backoff that hangs too, still end before the fail-closed
        deadline (see :attr:`renewal_reserve_seconds`). The cancellation
        timings do not scale: the grace is bound to the evolution handler's
        drain budget and the wait to an HTTP request, and neither has anything
        to do with how long a lease is.

        Raises:
            ValueError: If ``ttl_seconds`` is below ``MIN_LEASE_TTL_SECONDS``.
        """
        if ttl_seconds < MIN_LEASE_TTL_SECONDS:
            raise ValueError(
                f"a lease TTL of {ttl_seconds}s is below the minimum of "
                f"{MIN_LEASE_TTL_SECONDS}s"
            )
        scale = ttl_seconds / LEASE_TTL_SECONDS
        return cls(
            ttl_seconds=ttl_seconds,
            heartbeat_interval_seconds=HEARTBEAT_INTERVAL_SECONDS * scale,
            safety_margin_seconds=HEARTBEAT_SAFETY_MARGIN_SECONDS * scale,
            statement_timeout_seconds=min(DB_STATEMENT_TIMEOUT_SECONDS, ttl_seconds / 6),
            reconcile_interval_seconds=RECONCILE_INTERVAL_SECONDS * scale,
            active_task_sweep_interval_seconds=ACTIVE_TASK_SWEEP_INTERVAL_SECONDS * scale,
            orphan_task_age_seconds=ORPHAN_TASK_AGE_SECONDS * scale,
        )

    @property
    def working_window_seconds(self) -> float:
        """How long work may continue on a lease that was just confirmed."""
        return max(0.0, self.ttl_seconds - self.safety_margin_seconds)

    @property
    def renewal_reserve_seconds(self) -> float:
        """What is left of the working window in the worst renewal that still succeeds.

        The heartbeat ticks one interval after the last confirmed renewal; that
        renewal hangs for the whole statement timeout, the retry waits the
        longest backoff and hangs for the whole timeout too. Negative means the
        retry cannot finish before the deadline, and the heartbeat would cut
        it short and give the claim up.
        """
        longest_backoff = self.unknown_retry_seconds * (1 + UNKNOWN_RETRY_JITTER)
        return self.working_window_seconds - (
            self.heartbeat_interval_seconds
            + 2 * self.statement_timeout_seconds
            + longest_backoff
        )


def lease_settings_from_environment() -> LeaseSettings:
    """Lease timing for this process, scaled to its configured TTL."""
    from aion.server.settings import app_settings

    return LeaseSettings.for_ttl(app_settings.task_ownership_lease_ttl_seconds)


def reaper_enabled_by_environment() -> bool:
    """Whether this process reclaims expired leases; on unless turned off.

    Reads the parsed setting rather than the raw variable, so "off", "no" and
    "0" mean here what they mean everywhere else this deployment is
    configured.
    """
    from aion.server.settings import app_settings

    return app_settings.task_ownership_reaper

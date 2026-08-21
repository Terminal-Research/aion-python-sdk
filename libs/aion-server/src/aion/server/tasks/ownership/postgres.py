"""PostgreSQL-backed expiring leases: acquire, renew, release, and supervision."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta

from sqlalchemy import BigInteger, func, literal, select

from aion.db.postgres import db_manager as default_db_manager
from aion.db.postgres.manager import DbManager
from aion.db.postgres.models import TaskRecordModel
from aion.db.postgres.repositories import TaskClaimsRepository
from aion.server.tasks.identifiers import require_task_uuid

from .config import (
    LeaseSettings,
    RECONCILE_ADVISORY_LOCK_KEY,
    RECONCILER_ENV_VAR,
    reaper_enabled_by_environment,
)
from .heartbeat import OwnershipHeartbeat
from .reaper import ClaimReaper
from .types import (
    Busy,
    Claim,
    ControlSignal,
    ControlSignalCallback,
    Lost,
    Owned,
    OwnershipLossCallback,
    Unknown,
)

__all__ = ["PostgresOwnershipProvider"]

logger = logging.getLogger(__name__)


class PostgresOwnershipProvider:
    """Holds this process's leases and keeps them alive.

    The database is the authority; the claim map is only the receipt that lets
    a running task present its token to the fenced task store. There is no path
    from the map back into the database: a lease is created, extended and given
    up by exactly three statements, and nothing else writes that table.
    """

    enforcement_enabled = True

    def __init__(
        self,
        agent_id: str,
        *,
        db_manager: DbManager = default_db_manager,
        task_id_parser: Callable[[str], uuid.UUID] = require_task_uuid,
        owner_instance_id: str | None = None,
        settings: LeaseSettings | None = None,
        reconciler_enabled: bool | None = None,
    ) -> None:
        """Initialize a provider without opening a database connection.

        Args:
            agent_id: Identity of the agent this process serves. Every claim
                it acquires or reads is scoped to it, so several agents can
                share one database without fencing each other's leases.
            db_manager: Aion PostgreSQL manager used for short transactions.
            task_id_parser: Identifier parsing, shared with the task store so a
                lease and its task resolve to the same key.
            owner_instance_id: Optional diagnostic instance identity. When it
                is absent, the deployment-provided ``HOST_NAME`` is used.
            settings: Lease timing; the defaults are the deployed ones.
            reconciler_enabled: Whether this process reclaims expired leases.
                Defaults to the ``TASK_OWNERSHIP_REAPER`` switch, which is
                on unless explicitly disabled.
        """
        self.agent_id = agent_id
        self._db_manager = db_manager
        self._task_id_parser = task_id_parser
        self.settings = settings or LeaseSettings()
        self.owner_instance_id = owner_instance_id or os.getenv("HOST_NAME") or None
        self.reconciler_enabled = (
            reaper_enabled_by_environment()
            if reconciler_enabled is None
            else reconciler_enabled
        )

        self._claims: dict[str, Claim] = {}
        self._claims_lock = threading.Lock()
        # Two concurrent requests for one task must not both reach the
        # database: the second upsert would meet the first one's live lease and
        # report the process busy against itself.
        self._acquire_locks: dict[str, asyncio.Lock] = {}
        # How many acquires are currently using each lock. The lock exists for
        # the length of an attempt, not for the length of a lease: a refusal
        # has no lease to release later, and without a count the entry would
        # outlive every attempt that was ever refused.
        self._acquire_lock_users: dict[str, int] = {}
        self._loss_callback: OwnershipLossCallback | None = None
        self._control_signal_callback: ControlSignalCallback | None = None
        # Which (task_id, owner_token) incarnations already had a CANCEL
        # signal delivered to the registry. A cancellation request sits on
        # the claim row until the owner finishes acting on it, so every
        # renewal in between would otherwise re-report the same request -
        # this makes delivery a one-shot event per incarnation instead.
        # Bounded by the claim map itself: an entry is dropped wherever a
        # claim is, in release()/mark_lost(), and also on demand by
        # forget_control_signal() when a delivered signal was not acted on
        # successfully - so it never outlives its claim, and never survives
        # a failed delivery either.
        self._signaled_cancel: set[tuple[str, uuid.UUID]] = set()
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._reconciler_task: asyncio.Task[None] | None = None
        self._reconcile_lock = asyncio.Lock()
        self._reaper = ClaimReaper(
            db_manager=self._db_manager,
            settings=self.settings,
            owner_instance_id=self.owner_instance_id,
        )

    @property
    def claims(self) -> dict[str, Claim]:
        """Expose a defensive copy for diagnostics and focused unit tests."""
        with self._claims_lock:
            return dict(self._claims)

    # -- leases --------------------------------------------------------------

    async def acquire(self, task_id: str) -> Claim | Busy:
        """Acquire a free or expired lease using a conditional upsert."""
        task_uuid = self._task_id_parser(task_id)
        lock = self._checkout_acquire_lock(task_id)
        try:
            async with lock:
                return await self._acquire_locked(task_id, task_uuid)
        finally:
            # In a finally because a refusal and a database error are the two
            # outcomes that leave nothing behind to clean the entry up later.
            self._return_acquire_lock(task_id)

    def _checkout_acquire_lock(self, task_id: str) -> asyncio.Lock:
        """Return the per-task lock serialising acquires inside this process."""
        with self._claims_lock:
            lock = self._acquire_locks.get(task_id)
            if lock is None:
                lock = asyncio.Lock()
                self._acquire_locks[task_id] = lock
            self._acquire_lock_users[task_id] = self._acquire_lock_users.get(task_id, 0) + 1
            return lock

    def _return_acquire_lock(self, task_id: str) -> None:
        """Drop the lock once the last acquire using it has finished.

        Counted rather than read off ``asyncio.Lock.locked()``. Releasing a
        lock wakes the next waiter through the event loop, so between the two
        the lock reports itself free while an acquire is still queued on it.
        Dropping the entry in that window lets the next caller build a second
        lock and reach the database beside the one being woken - the very race
        the lock exists to prevent.
        """
        with self._claims_lock:
            remaining = self._acquire_lock_users.get(task_id, 0) - 1
            if remaining > 0:
                self._acquire_lock_users[task_id] = remaining
                return
            self._acquire_lock_users.pop(task_id, None)
            self._acquire_locks.pop(task_id, None)

    async def _acquire_locked(self, task_id: str, task_uuid: uuid.UUID) -> Claim | Busy:
        """Run one acquire while this process holds the per-task lock."""
        with self._claims_lock:
            current = self._claims.get(task_id)
        if current is not None:
            return current

        started = time.monotonic()
        token = uuid.uuid4()
        ttl = timedelta(seconds=self.settings.ttl_seconds)

        try:
            async with asyncio.timeout(self.settings.statement_timeout_seconds):
                async with self._db_manager.get_session() as session:
                    # Task row first, the lock order every writer follows. A new
                    # task has no row yet, so for it the claim upsert is itself
                    # the serialization point.
                    await session.execute(
                        select(TaskRecordModel.id)
                        .where(TaskRecordModel.id == task_uuid)
                        .with_for_update()
                    )
                    record = await TaskClaimsRepository(session).acquire(
                        task_uuid, self.agent_id, token, ttl, self.owner_instance_id
                    )
                    await session.commit()
        except Exception:
            logger.warning("Task claim acquire failed for %s", task_id, exc_info=True)
            raise

        if record is None:
            logger.debug("Task %s is owned by another instance", task_id)
            return Busy()

        claim = Claim(
            task_id=task_id,
            owner_token=record.owner_token,
            lease_expires_at=record.lease_expires_at,
            deadline=self._safe_deadline(started),
        )
        with self._claims_lock:
            self._claims[task_id] = claim
        return claim

    async def renew(self, claim: Claim) -> Owned | Lost | Unknown:
        """Conditionally extend a lease and distinguish loss from uncertainty.

        Not a poll: the write is needed for liveness regardless, and the answer
        to "has this been taken from me" arrives as its byproduct.
        """
        task_uuid = self._task_id_parser(claim.task_id)
        started = time.monotonic()
        ttl = timedelta(seconds=self.settings.ttl_seconds)
        try:
            # A hung connection answers neither way. Without this bound the
            # supervisor parks here and the fail-closed deadline, which is only
            # checked between attempts, never runs.
            async with asyncio.timeout(self.settings.statement_timeout_seconds):
                async with self._db_manager.get_session() as session:
                    record = await TaskClaimsRepository(session).renew(
                        task_uuid, claim.owner_token, ttl
                    )
                    await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return Unknown(exc)

        if record is None:
            # A definitive loss. The receipt goes now and the registry is told
            # synchronously; the heartbeat's follow-up ``mark_lost`` is then an
            # idempotent no-op.
            self.mark_lost(claim, "renew_lost")
            return Lost()

        updated = replace(
            claim,
            lease_expires_at=record.lease_expires_at,
            deadline=self._safe_deadline(started),
        )
        with self._claims_lock:
            if self._same_claim(self._claims.get(claim.task_id), claim):
                self._claims[claim.task_id] = updated
        if record.cancel_requested_at is not None:
            self._notify_control_signal_once(claim.task_id, claim.owner_token, ControlSignal.CANCEL)
        return Owned(record.lease_expires_at, cancel_requested=record.cancel_requested_at is not None)

    async def renew_batch(self, claims: list[Claim]) -> dict[str, Owned | Lost] | Unknown:
        """Conditionally extend many leases in one round trip.

        Same fencing and result semantics as :meth:`renew`, batched: a
        statement that executes is definitive for every claim in it - some
        renew, some do not, exactly as calling :meth:`renew` once per claim
        would report - so only a failed or timed-out statement is uncertain,
        and it is uncertain for the whole batch at once rather than as one
        ``Unknown`` per claim.

        Returns:
            A mapping from ``task_id`` to its outcome when the statement
            executed, or a single :class:`Unknown` for the whole batch when
            it did not.
        """
        if not claims:
            return {}

        started = time.monotonic()
        ttl = timedelta(seconds=self.settings.ttl_seconds)
        try:
            pairs = [(self._task_id_parser(claim.task_id), claim.owner_token) for claim in claims]
            async with asyncio.timeout(self.settings.statement_timeout_seconds):
                async with self._db_manager.get_session() as session:
                    records = await TaskClaimsRepository(session).renew_batch(pairs, ttl)
                    await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return Unknown(exc)

        renewed_by_task_id = {str(record.task_id): record for record in records}
        deadline = self._safe_deadline(started)
        results: dict[str, Owned | Lost] = {}
        for claim in claims:
            record = renewed_by_task_id.get(claim.task_id)
            if record is None:
                # A definitive loss, reported the same way renew() reports
                # one: the receipt goes now, synchronously, so the follow-up
                # mark_lost the caller does for bookkeeping is idempotent.
                self.mark_lost(claim, "renew_lost")
                results[claim.task_id] = Lost()
                continue

            updated = replace(
                claim,
                lease_expires_at=record.lease_expires_at,
                deadline=deadline,
            )
            with self._claims_lock:
                if self._same_claim(self._claims.get(claim.task_id), claim):
                    self._claims[claim.task_id] = updated
            cancel_requested = record.cancel_requested_at is not None
            if cancel_requested:
                self._notify_control_signal_once(
                    claim.task_id, claim.owner_token, ControlSignal.CANCEL
                )
            results[claim.task_id] = Owned(record.lease_expires_at, cancel_requested=cancel_requested)
        return results

    async def release(self, claim: Claim) -> None:
        """Conditionally delete a lease and always forget the local receipt."""
        task_uuid = self._task_id_parser(claim.task_id)
        try:
            async with asyncio.timeout(self.settings.statement_timeout_seconds):
                async with self._db_manager.get_session() as session:
                    await TaskClaimsRepository(session).release(task_uuid, claim.owner_token)
                    await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Best-effort relinquishment. Forgetting the local receipt stops
            # this process from continuing work either way, and a row left
            # behind by an unreachable database expires on its own.
            logger.warning("Task claim release failed for %s", claim.task_id, exc_info=True)
        finally:
            with self._claims_lock:
                if self._same_claim(self._claims.get(claim.task_id), claim):
                    self._claims.pop(claim.task_id, None)
                self._signaled_cancel.discard((claim.task_id, claim.owner_token))

    def claim_for(self, task_id: str) -> Claim | None:
        """Return the locally remembered lease without querying PostgreSQL.

        Named for what it hands over rather than what it might mean: it is the
        token to present, never a judgment about who owns the task.
        """
        with self._claims_lock:
            return self._claims.get(task_id)

    async def current_owner(self, task_id: str) -> str | None:
        """Read who currently holds the lease, purely for diagnostics.

        A plain, unconditional read — not part of the fencing predicate and not
        used to decide anything on this side. It exists so a ``Busy`` refusal
        can name the instance a caller might want to reach, if something in
        front of this server is able to act on that name.
        """
        task_uuid = self._task_id_parser(task_id)
        try:
            async with asyncio.timeout(self.settings.statement_timeout_seconds):
                async with self._db_manager.get_session() as session:
                    record = await TaskClaimsRepository(session).find_by_task_id(
                        task_uuid, self.agent_id
                    )
        except Exception:
            logger.debug(
                "Could not read the current owner of %s for diagnostics",
                task_id,
                exc_info=True,
            )
            return None
        return record.owner_instance_id if record is not None else None

    def snapshot(self) -> list[Claim]:
        """Return a stable copy of all locally remembered leases."""
        with self._claims_lock:
            return list(self._claims.values())

    def _safe_deadline(self, started: float) -> float:
        """Return the local fail-closed deadline for a confirmed lease."""
        return started + self.settings.working_window_seconds

    @staticmethod
    def _same_claim(left: Claim | None, right: Claim) -> bool:
        """Compare leases by token, ignoring diagnostic timestamps."""
        return left is not None and left.owner_token == right.owner_token

    # -- loss ----------------------------------------------------------------

    def set_loss_callback(self, callback: OwnershipLossCallback | None) -> None:
        """Register the callback invoked once fail-closed ownership is lost."""
        self._loss_callback = callback

    def set_control_signal_callback(self, callback: ControlSignalCallback | None) -> None:
        """Register the callback invoked once a held claim carries a new request."""
        self._control_signal_callback = callback

    def forget_control_signal(self, task_id: str) -> None:
        """Let the next renewal re-deliver a signal this process failed to act on.

        Scoped to the token this process currently holds for ``task_id``:
        there is nothing to forget for a task whose claim already moved on or
        was never held here, and this must never touch a different
        incarnation's bookkeeping by accident.
        """
        claim = self.claim_for(task_id)
        if claim is None:
            return
        with self._claims_lock:
            self._signaled_cancel.discard((task_id, claim.owner_token))

    def mark_lost(self, claim: Claim, reason: str) -> None:
        """Forget a lease only if the map still contains that incarnation."""
        with self._claims_lock:
            if not self._same_claim(self._claims.get(claim.task_id), claim):
                return
            self._claims.pop(claim.task_id, None)
            self._signaled_cancel.discard((claim.task_id, claim.owner_token))
        self._notify_loss(claim.task_id, reason)

    def _notify_loss(self, task_id: str, reason: str) -> None:
        """Notify the registry without letting a callback kill the heartbeat."""
        callback = self._loss_callback
        if callback is None:
            return
        try:
            callback(task_id, reason)
        except Exception:
            logger.exception("Ownership-loss callback failed for %s", task_id)

    def _notify_control_signal_once(
        self, task_id: str, owner_token: uuid.UUID, signal: ControlSignal
    ) -> None:
        """Deliver a signal to the registry exactly once per claim incarnation.

        The request sits on the claim row for as long as the owner takes to
        act on it, so every renewal in between reads it again - deduplication
        happens here, not at the database, which stays a plain fenced read.
        """
        key = (task_id, owner_token)
        with self._claims_lock:
            if key in self._signaled_cancel:
                return
            self._signaled_cancel.add(key)
        callback = self._control_signal_callback
        if callback is None:
            return
        try:
            callback(task_id, signal)
        except Exception:
            logger.exception("Control-signal callback failed for %s", task_id)

    # -- supervision ---------------------------------------------------------

    def start(self) -> None:
        """Start the heartbeat, and the reaper if this process runs one."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Said out loud rather than passed over: without the heartbeat every
            # lease this process takes expires on schedule while it keeps
            # executing, which is the one failure the whole mechanism exists to
            # prevent, and it would otherwise be invisible.
            logger.error(
                "Task ownership supervision could not start: no running event "
                "loop. Leases will not be renewed."
            )
            return

        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = loop.create_task(
                OwnershipHeartbeat(self).run(),
                name="task-ownership-heartbeat",
            )
            self._heartbeat_task.add_done_callback(self._on_heartbeat_task_done)
        if not self.reconciler_enabled:
            logger.info(
                "Task ownership reaper is disabled; set %s to enable it",
                RECONCILER_ENV_VAR,
            )
            return
        if self._reconciler_task is None or self._reconciler_task.done():
            self._reconciler_task = loop.create_task(
                self._reconcile_loop(),
                name="task-ownership-reconciler",
            )
            self._reconciler_task.add_done_callback(self._on_reconciler_task_done)

    def _on_heartbeat_task_done(self, task: asyncio.Task[None]) -> None:
        """Restart the heartbeat if it ended on its own rather than via stop().

        ``OwnershipHeartbeat.run`` only returns by raising, and it already
        fails closed on every claim it knows about before letting an
        exception escape - see ``_renew_all``. So the only two ways this
        callback fires are an expected cancellation from :meth:`stop`, and a
        bug that escaped that handling anyway. The first is silent on
        purpose: :meth:`stop` has already cleared ``_heartbeat_task``, which
        is what tells the two apart here without a separate flag.

        The second must not be silent. A heartbeat that stopped running is
        the one failure this whole mechanism exists to prevent: every lease
        already in the claim map would sit unrenewed while the executions
        holding them keep going, discovered only whenever each next happens
        to attempt a write. Restarting immediately does not undo whatever
        renewals were missed, but it puts every claim back under active
        supervision rather than leaving them to be found one write at a time.
        """
        if task is not self._heartbeat_task:
            # stop() always clears the reference before it cancels the task,
            # so a mismatch here means that path already handled this exit.
            return
        if task.cancelled():
            logger.error("Task ownership heartbeat was cancelled outside stop(); restarting")
        else:
            logger.error(
                "Task ownership heartbeat crashed; restarting", exc_info=task.exception()
            )
        self._heartbeat_task = None
        self.start()

    def _on_reconciler_task_done(self, task: asyncio.Task[None]) -> None:
        """Restart the reconciler if it ended on its own rather than via stop().

        Milder than losing the heartbeat - nothing this process is executing
        is put at risk, since fencing on write is what actually protects a
        claim, not the reconciler being alive. But a dead reconciler is
        silent: no lease anywhere reports it, no execution notices, and this
        pod simply stops helping reclaim leases other processes abandoned.
        In a single-pod deployment, or if every pod hit the same bug, nothing
        would ever reap an orphaned lease again. Same detection as
        :meth:`_on_heartbeat_task_done`: :meth:`stop` clears the reference
        before it cancels the task, so a mismatch here means that path
        already handled this exit.
        """
        if task is not self._reconciler_task:
            return
        if task.cancelled():
            logger.error("Task ownership reconciler was cancelled outside stop(); restarting")
        else:
            logger.error(
                "Task ownership reconciler crashed; restarting", exc_info=task.exception()
            )
        self._reconciler_task = None
        self.start()

    async def stop(self) -> None:
        """Cancel and await the heartbeat and reconciler tasks."""
        tasks = [
            task
            for task in (self._heartbeat_task, self._reconciler_task)
            if task is not None
        ]
        self._heartbeat_task = None
        self._reconciler_task = None
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _reconcile_loop(self) -> None:
        """Run periodic reconciliation without overlapping passes.

        Two cadences share one wake-up timer. The claim-expiry pass runs
        every tick, at ``reconcile_interval_seconds``; the active-task sweep
        - the rarer, defensive pass, see :meth:`ClaimReaper.run_once` - only
        piggybacks on a tick once its own, longer
        ``active_task_sweep_interval_seconds`` has elapsed. Both are timed
        from when they last ran, the same discipline the heartbeat uses, so
        neither drifts against the other from a slow tick.
        """
        next_run = time.monotonic() + self.settings.reconcile_interval_seconds
        next_sweep = time.monotonic() + self.settings.active_task_sweep_interval_seconds
        while True:
            delay = next_run - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            started = time.monotonic()
            include_sweep = started >= next_sweep
            try:
                await self.reconcile(include_active_task_sweep=include_sweep)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Task ownership reconciliation failed", exc_info=True)
            next_run = started + self.settings.reconcile_interval_seconds
            if include_sweep:
                next_sweep = started + self.settings.active_task_sweep_interval_seconds

    async def reconcile(self, *, include_active_task_sweep: bool = True) -> int:
        """Run one reaper pass, if this process is allowed to reclaim leases."""
        if not self.reconciler_enabled:
            return 0
        async with self._reconcile_lock:
            return await self._reconcile_with_cluster_lock(include_active_task_sweep)

    async def _reconcile_with_cluster_lock(self, include_active_task_sweep: bool) -> int:
        """Run one reaper pass only while this process holds the cluster-wide lock.

        Every process reconciles against the same claims and tasks, so
        letting all of them run every tick means every one of them reads the
        same candidate list and most lose the race for each row to
        ``FOR UPDATE SKIP LOCKED`` - correct, but wasted work that scales with
        pod count rather than with orphans found. A non-blocking advisory
        lock picks one worker per tick instead: a pod that does not get it
        skips the tick outright rather than queuing behind whichever pod is
        already reaping, and tries again on the next one regardless.

        The lock lives on its own connection, held only for the duration of
        this one call and never handed to the reaper's own short
        transactions - those keep using the pool as normal. Session-scoped,
        so a pod that dies mid-tick releases it the moment its connection
        closes, with nothing here depending on that cleanup running.
        """
        # Typed explicitly: the key exceeds a 32-bit int, and an untyped
        # literal would leave the driver to guess the parameter's OID.
        lock_key = literal(RECONCILE_ADVISORY_LOCK_KEY, type_=BigInteger)
        engine = self._db_manager.get_engine()
        async with engine.connect() as connection:
            acquired = (
                await connection.execute(select(func.pg_try_advisory_lock(lock_key)))
            ).scalar()
            await connection.commit()
            if not acquired:
                return 0
            try:
                return await self._reaper.run_once(
                    include_active_task_sweep=include_active_task_sweep
                )
            finally:
                try:
                    await connection.execute(select(func.pg_advisory_unlock(lock_key)))
                    await connection.commit()
                except Exception:
                    # Best-effort: a dead connection cannot be unlocked, but
                    # Postgres already releases a session-scoped lock when the
                    # session that holds it closes, whatever killed it here.
                    logger.warning(
                        "Failed to release reconcile advisory lock", exc_info=True
                    )

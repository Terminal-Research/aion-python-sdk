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

from sqlalchemy import select

from aion.db.postgres import db_manager as default_db_manager
from aion.db.postgres.manager import DbManager
from aion.db.postgres.models import TaskRecordModel
from aion.db.postgres.repositories import TaskClaimsRepository
from aion.server.tasks.identifiers import require_task_uuid

from .config import LeaseSettings, RECONCILER_ENV_VAR, reaper_enabled_by_environment
from .heartbeat import OwnershipHeartbeat
from .reaper import ClaimReaper
from .types import Busy, Claim, Lost, Owned, OwnershipLossCallback, Unknown

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
        *,
        db_manager: DbManager = default_db_manager,
        task_id_parser: Callable[[str], uuid.UUID] = require_task_uuid,
        owner_instance_id: str | None = None,
        settings: LeaseSettings | None = None,
        reconciler_enabled: bool | None = None,
    ) -> None:
        """Initialize a provider without opening a database connection.

        Args:
            db_manager: Aion PostgreSQL manager used for short transactions.
            task_id_parser: Identifier parsing, shared with the task store so a
                lease and its task resolve to the same key.
            owner_instance_id: Optional diagnostic pod identity.
            settings: Lease timing; the defaults are the deployed ones.
            reconciler_enabled: Whether this process reclaims expired leases.
                Defaults to the ``AION_TASK_OWNERSHIP_REAPER`` switch, which is
                on unless explicitly disabled.
        """
        self._db_manager = db_manager
        self._task_id_parser = task_id_parser
        self.settings = settings or LeaseSettings()
        self.owner_instance_id = (
            owner_instance_id
            if owner_instance_id is not None
            else os.getenv("POD_NAME") or os.getenv("HOSTNAME") or None
        )
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
                        task_uuid, token, ttl, self.owner_instance_id
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
        return Owned(record.lease_expires_at)

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
                    record = await TaskClaimsRepository(session).find_by_task_id(task_uuid)
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

    def mark_lost(self, claim: Claim, reason: str) -> None:
        """Forget a lease only if the map still contains that incarnation."""
        with self._claims_lock:
            if not self._same_claim(self._claims.get(claim.task_id), claim):
                return
            self._claims.pop(claim.task_id, None)
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
        """Run periodic reconciliation without overlapping passes."""
        next_run = time.monotonic() + self.settings.reconcile_interval_seconds
        while True:
            delay = next_run - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            started = time.monotonic()
            try:
                await self.reconcile()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Task ownership reconciliation failed", exc_info=True)
            next_run = started + self.settings.reconcile_interval_seconds

    async def reconcile(self) -> int:
        """Run one reaper pass, if this process is allowed to reclaim leases."""
        if not self.reconciler_enabled:
            return 0
        async with self._reconcile_lock:
            return await self._reaper.run_once()

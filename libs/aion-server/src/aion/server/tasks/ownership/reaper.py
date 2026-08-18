"""Reclaiming leases whose owner stopped renewing them.

The reaper is the layer that makes ownership survive a process that dies
without saying anything: an expired lease is the only automatic signal that a
task may be taken over, and this is what acts on it.

It is deliberately separate from the provider that hands out leases. Holding a
lease and reclaiming someone else's are different jobs with different failure
consequences, and only one of them is safe to enable on the first deployment.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import text

from aion.core.a2a.enums import TaskSettlementReason
from aion.db.postgres.manager import DbManager
from aion.db.postgres.records import TaskRecord
from aion.db.postgres.repositories import TasksRepository
from aion.server.a2a.constants import ACTIVE_TASK_STATES
from aion.server.tasks.identifiers import as_uuid
from aion.server.tasks.settlement import settled_task
from a2a.types import TaskState

from .config import LeaseSettings

__all__ = ["ClaimReaper"]

logger = logging.getLogger(__name__)


class _ReconcileAbort(Exception):
    """Internal signal that one candidate must be rolled back.

    A candidate is examined inside a transaction that may already have replaced
    a fencing token or inserted a temporary lease, so abandoning it has to undo
    those writes. Raising out of the transaction block is the only way to do
    that: returning from inside it commits.
    """


class ClaimReaper:
    """Settles tasks whose owner is gone, one short transaction at a time."""

    def __init__(
        self,
        db_manager: DbManager,
        settings: LeaseSettings,
        owner_instance_id: str | None = None,
    ) -> None:
        """Bind the reaper to a database and the timing it judges leases by.

        Args:
            db_manager: Aion PostgreSQL manager used for short transactions.
            settings: Lease timing, for the batch size and the orphan age.
            owner_instance_id: Diagnostic identity written on temporary leases.
        """
        self._db_manager = db_manager
        self._settings = settings
        self._owner_instance_id = owner_instance_id

    async def run_once(self) -> int:
        """Run both passes and report how many tasks were settled."""
        settled = 0
        for task_uuid in await self._expired_claim_candidates():
            if await self._settle_expired_claim(task_uuid):
                settled += 1
        for task_uuid in await self._unowned_active_task_candidates():
            if await self._settle_unowned_active_task(task_uuid):
                settled += 1
        return settled

    async def _expired_claim_candidates(self) -> list[uuid.UUID]:
        """Read a bounded, lock-free hypothesis of expired lease rows.

        No locks on purpose. Two processes starting a pass together get the
        same list, which is fine: the list is a hypothesis, and every candidate
        is rechecked under a lock before anything is written.
        """
        async with self._db_manager.get_session() as session:
            result = await session.execute(
                text(
                    "SELECT task_id FROM task_claims "
                    "WHERE lease_expires_at <= clock_timestamp() "
                    "ORDER BY lease_expires_at LIMIT :limit"
                ),
                {"limit": self._settings.reconcile_batch_size},
            )
            return [as_uuid(row[0]) for row in result.fetchall()]

    async def _unowned_active_task_candidates(self) -> list[uuid.UUID]:
        """Read old active tasks that have no lease, without taking locks.

        A task presented as running with no lease at all falls through the
        expiry index entirely and would hang forever. Interrupted states are
        excluded by ``ACTIVE_TASK_STATES``: a task waiting for a user has no
        owner on purpose.
        """
        async with self._db_manager.get_session() as session:
            result = await session.execute(
                text(
                    "SELECT id FROM tasks "
                    "WHERE state = ANY(CAST(:states AS text[])) "
                    "  AND updated_at <= clock_timestamp() - CAST(:age AS interval) "
                    "  AND NOT EXISTS (SELECT 1 FROM task_claims "
                    "                  WHERE task_claims.task_id = tasks.id) "
                    "ORDER BY updated_at LIMIT :limit"
                ),
                {
                    "states": self._active_state_names(),
                    "age": f"{self._settings.orphan_task_age_seconds} seconds",
                    "limit": self._settings.reconcile_batch_size,
                },
            )
            return [as_uuid(row[0]) for row in result.fetchall()]

    @staticmethod
    def _active_state_names() -> list[str]:
        """Return the state names the database stores for running tasks."""
        return [TaskState.Name(state) for state in ACTIVE_TASK_STATES]

    async def _settle_expired_claim(self, task_uuid: uuid.UUID) -> bool:
        """Recheck and settle one expired lease in its own short transaction."""
        token = uuid.uuid4()
        try:
            async with self._db_manager.get_session() as session:
                async with session.begin():
                    # A lease is acquired before the first task row is written,
                    # so an expired lease with no task behind it is an orphan
                    # from a process that died in that window. Nothing will ever
                    # settle it; only this delete keeps the table bounded. The
                    # expiry predicate makes it safe against a fresh acquire,
                    # which moves the expiry into the future.
                    exists = await session.execute(
                        text("SELECT id FROM tasks WHERE id = :task_id"),
                        {"task_id": task_uuid},
                    )
                    if exists.first() is None:
                        await session.execute(
                            text(
                                "DELETE FROM task_claims "
                                " WHERE task_id = :task_id "
                                "   AND lease_expires_at <= clock_timestamp()"
                            ),
                            {"task_id": task_uuid},
                        )
                        return False

                    # Task row first (the lock order every writer follows), and
                    # skipped rather than awaited: an unavailable row means
                    # another process is already on this candidate.
                    task_row = await session.execute(
                        text(
                            "SELECT id FROM tasks WHERE id = :task_id "
                            "FOR UPDATE SKIP LOCKED"
                        ),
                        {"task_id": task_uuid},
                    )
                    if task_row.first() is None:
                        raise _ReconcileAbort

                    # Rechecked under the lock. This is what makes the lock-free
                    # candidate query safe: the owner may have renewed in
                    # between.
                    claim_row = await session.execute(
                        text(
                            "SELECT lease_expires_at FROM task_claims "
                            "WHERE task_id = :task_id "
                            "  AND lease_expires_at <= clock_timestamp() "
                            "FOR UPDATE"
                        ),
                        {"task_id": task_uuid},
                    )
                    if claim_row.first() is None:
                        raise _ReconcileAbort

                    # Replacing the token shuts out a revived owner that would
                    # otherwise write between the decision and the outcome.
                    await session.execute(
                        text(
                            "UPDATE task_claims SET owner_token = :new_token "
                            "WHERE task_id = :task_id"
                        ),
                        {"task_id": task_uuid, "new_token": token},
                    )
                    return await self._settle_locked_task(session, task_uuid, token)
        except _ReconcileAbort:
            return False

    async def _settle_unowned_active_task(self, task_uuid: uuid.UUID) -> bool:
        """Recheck and settle one old active task that lacks a lease."""
        token = uuid.uuid4()
        try:
            async with self._db_manager.get_session() as session:
                async with session.begin():
                    task_row = await session.execute(
                        text(
                            "SELECT id FROM tasks "
                            "WHERE id = :task_id "
                            "  AND state = ANY(CAST(:states AS text[])) "
                            "  AND updated_at <= clock_timestamp() - CAST(:age AS interval) "
                            "  AND NOT EXISTS (SELECT 1 FROM task_claims "
                            "                  WHERE task_claims.task_id = tasks.id) "
                            "FOR UPDATE SKIP LOCKED"
                        ),
                        {
                            "task_id": task_uuid,
                            "states": self._active_state_names(),
                            "age": f"{self._settings.orphan_task_age_seconds} seconds",
                        },
                    )
                    if task_row.first() is None:
                        raise _ReconcileAbort

                    # A lease of our own, so the outcome below is written under
                    # the same fencing rule as any other write.
                    inserted = await session.execute(
                        text(
                            "INSERT INTO task_claims "
                            "(task_id, owner_token, lease_expires_at, owner_instance_id) "
                            "VALUES (:task_id, :owner_token, "
                            "        clock_timestamp() + CAST(:ttl AS interval), "
                            "        :owner_instance_id) "
                            "ON CONFLICT (task_id) DO NOTHING "
                            "RETURNING task_id"
                        ),
                        {
                            "task_id": task_uuid,
                            "owner_token": token,
                            "ttl": f"{self._settings.ttl_seconds} seconds",
                            "owner_instance_id": self._owner_instance_id,
                        },
                    )
                    if inserted.first() is None:
                        raise _ReconcileAbort
                    return await self._settle_locked_task(session, task_uuid, token)
        except _ReconcileAbort:
            return False

    async def _settle_locked_task(
        self,
        session,
        task_uuid: uuid.UUID,
        token: uuid.UUID,
    ) -> bool:
        """Write a fenced restart outcome and release the temporary lease.

        Returns:
            ``True`` when an outcome was recorded for the task, ``False`` when
            only a stale lease had to go. Both commit.

        Raises:
            _ReconcileAbort: If the fencing write was refused, which rolls the
                whole candidate back rather than leaving a replaced token
                behind.
        """
        repository = TasksRepository(session)
        entity = await repository.find_by_id(task_uuid)
        settled = (
            settled_task(entity.to_task(str(task_uuid)), TaskSettlementReason.SERVER_RESTART)
            if entity is not None
            else None
        )
        if settled is None:
            # No task row, or one that is terminal or waiting for input on
            # purpose. The lease is still stale and must not be rediscovered
            # every pass, but the task itself must remain untouched.
            await self._delete_claim(session, task_uuid, token)
            return False

        if not await repository.save_owned(TaskRecord.from_task(settled), token):
            raise _ReconcileAbort
        await self._delete_claim(session, task_uuid, token)
        return True

    @staticmethod
    async def _delete_claim(session, task_uuid: uuid.UUID, token: uuid.UUID) -> None:
        """Delete only the temporary fencing incarnation used by settlement."""
        await session.execute(
            text(
                "DELETE FROM task_claims "
                "WHERE task_id = :task_id AND owner_token = :owner_token"
            ),
            {"task_id": task_uuid, "owner_token": token},
        )

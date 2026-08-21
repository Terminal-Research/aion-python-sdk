"""Reclaiming leases whose owner stopped renewing them.

The reaper is the layer that makes ownership survive a process that dies
without saying anything: an expired lease is the only automatic signal that a
task may be taken over, and this is what acts on it.

It is deliberately separate from the provider that hands out leases. Holding a
lease and reclaiming someone else's are different jobs with different failure
consequences, and only one of them is safe to enable on the first deployment.
"""

from __future__ import annotations

import datetime as _dt
import logging
import uuid

from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from aion.core.a2a.enums import TaskSettlementReason
from aion.db.postgres.manager import DbManager
from aion.db.postgres.models import TaskClaimModel, TaskRecordModel
from aion.db.postgres.records import TaskRecord
from aion.db.postgres.repositories import TaskClaimsRepository, TasksRepository
from aion.server.a2a.constants import ACTIVE_TASK_STATES
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

    async def run_once(self, *, include_active_task_sweep: bool = True) -> int:
        """Run the claim-expiry pass, and the active-task sweep when due.

        Args:
            include_active_task_sweep: Whether to also run
                :meth:`_unowned_active_task_candidates` this call. The claim
                table is the everyday source of truth for what expired, and
                the caller runs that pass every tick; the sweep exists only
                for a task presented as active with no lease behind it at
                all, which the expiry index can never surface. That case
                should not occur, so the caller checks it far less often -
                see ``ACTIVE_TASK_SWEEP_INTERVAL_SECONDS``.
        """
        await self._delete_orphan_claims()
        settled = 0
        for task_uuid in await self._expired_claim_candidates():
            if await self._settle_expired_claim(task_uuid):
                settled += 1
        if include_active_task_sweep:
            for task_uuid in await self._unowned_active_task_candidates():
                if await self._settle_unowned_active_task(task_uuid):
                    settled += 1
        return settled

    async def _delete_orphan_claims(self) -> None:
        """Remove expired leases whose task row is gone, as their own pass.

        An empty ``FOR UPDATE SKIP LOCKED`` result means one of two different
        things: the row does not exist, or another process holds it right
        now. Treating an empty result as "gone" in the settlement passes
        would delete a live claim out from under its holder; treating it as
        "busy" always would mean a genuine orphan - the task row was deleted
        with its claim left behind - is never removed.

        This batch delete is the only place that decides "gone", and it does
        so with a plain, unlocked ``NOT EXISTS`` rather than by racing a
        settlement transaction for the row. A claim missed here because the
        task was deleted between this statement and a settlement attempt is
        caught on the next pass.
        """
        async with self._db_manager.get_session() as session:
            await session.execute(
                delete(TaskClaimModel).where(
                    TaskClaimModel.lease_expires_at <= func.statement_timestamp(),
                    ~exists().where(TaskRecordModel.id == TaskClaimModel.task_id),
                )
            )
            await session.commit()

    async def _expired_claim_candidates(self) -> list[uuid.UUID]:
        """Read a bounded, lock-free hypothesis of expired lease rows.

        No locks on purpose. Two processes starting a pass together get the
        same list, which is fine: the list is a hypothesis, and every candidate
        is rechecked under a lock before anything is written.
        """
        async with self._db_manager.get_session() as session:
            result = await session.execute(
                select(TaskClaimModel.task_id)
                .where(TaskClaimModel.lease_expires_at <= func.statement_timestamp())
                .order_by(TaskClaimModel.lease_expires_at)
                .limit(self._settings.reconcile_batch_size)
            )
            return list(result.scalars().all())

    async def _unowned_active_task_candidates(self) -> list[uuid.UUID]:
        """Read old active tasks that have no lease, without taking locks.

        A task presented as running with no lease at all falls through the
        expiry index entirely and would hang forever. Interrupted states are
        excluded by ``ACTIVE_TASK_STATES``: a task waiting for a user has no
        owner on purpose.
        """
        threshold = func.statement_timestamp() - _dt.timedelta(
            seconds=self._settings.orphan_task_age_seconds
        )
        async with self._db_manager.get_session() as session:
            result = await session.execute(
                select(TaskRecordModel.id)
                .where(
                    TaskRecordModel.state.in_(self._active_state_names()),
                    TaskRecordModel.updated_at <= threshold,
                    ~exists().where(TaskClaimModel.task_id == TaskRecordModel.id),
                )
                .order_by(TaskRecordModel.updated_at)
                .limit(self._settings.reconcile_batch_size)
            )
            return list(result.scalars().all())

    @staticmethod
    def _active_state_names() -> list[str]:
        """Return the state names the database stores for running tasks."""
        return [TaskState.Name(state) for state in ACTIVE_TASK_STATES]

    async def _settle_expired_claim(self, task_uuid: uuid.UUID) -> bool:
        """Recheck and settle one expired lease in its own short transaction.

        A task row missing here is not this pass's concern: it means either
        the claim is an orphan, which :meth:`_delete_orphan_claims` removes
        on its own pass, or the row was deleted between the candidate read
        and this transaction, which the next pass sees as an orphan too.
        """
        token = uuid.uuid4()
        try:
            async with self._db_manager.get_session() as session:
                async with session.begin():
                    repository = TasksRepository(session)

                    # Task row first (the lock order every writer follows),
                    # skipped rather than waited on: an unavailable row means
                    # another process is already on this candidate. Read in
                    # full so the settlement below never re-fetches it.
                    entity = await repository.find_by_id_for_update(
                        task_uuid, skip_locked=True
                    )
                    if entity is None:
                        raise _ReconcileAbort

                    # Recheck and fence in the same statement: a claim that no
                    # longer matches the expiry predicate was renewed between
                    # the lock-free candidate read and this transaction, which
                    # is exactly what the lock-free read has to tolerate.
                    # Replacing the token also shuts out a revived owner that
                    # would otherwise write between this decision and the
                    # outcome below.
                    claim_row = await session.execute(
                        update(TaskClaimModel)
                        .where(
                            TaskClaimModel.task_id == task_uuid,
                            TaskClaimModel.lease_expires_at <= func.statement_timestamp(),
                        )
                        .values(owner_token=token)
                        .returning(TaskClaimModel.task_id)
                    )
                    if claim_row.first() is None:
                        raise _ReconcileAbort

                    return await self._settle_locked_task(
                        session, repository, task_uuid, token, entity
                    )
        except _ReconcileAbort:
            return False

    async def _settle_unowned_active_task(self, task_uuid: uuid.UUID) -> bool:
        """Recheck and settle one old active task that lacks a lease."""
        token = uuid.uuid4()
        try:
            async with self._db_manager.get_session() as session:
                async with session.begin():
                    repository = TasksRepository(session)

                    # Locked and read in full up front, so settlement below
                    # never re-fetches this row. The candidate query's other
                    # predicates - active state, age, no existing claim - are
                    # rechecked in Python against this same locked read rather
                    # than repeated in SQL.
                    entity = await repository.find_by_id_for_update(
                        task_uuid, skip_locked=True
                    )
                    if entity is None or not self._is_still_unowned_active(entity):
                        raise _ReconcileAbort

                    claim_row = await session.execute(
                        select(TaskClaimModel.task_id).where(
                            TaskClaimModel.task_id == task_uuid
                        )
                    )
                    if claim_row.first() is not None:
                        raise _ReconcileAbort

                    # A lease of our own, so the outcome below is written under
                    # the same fencing rule as any other write. agent_id comes
                    # from the task row just locked above, not this process's
                    # own identity: the reaper settles orphans for whichever
                    # agent the task actually belongs to.
                    insert_stmt = pg_insert(TaskClaimModel).values(
                        task_id=task_uuid,
                        agent_id=entity.agent_id,
                        owner_token=token,
                        lease_expires_at=func.clock_timestamp()
                        + _dt.timedelta(seconds=self._settings.ttl_seconds),
                        owner_instance_id=self._owner_instance_id,
                    )
                    inserted = await session.execute(
                        insert_stmt.on_conflict_do_nothing(
                            index_elements=[TaskClaimModel.task_id]
                        ).returning(TaskClaimModel.task_id)
                    )
                    if inserted.first() is None:
                        raise _ReconcileAbort
                    return await self._settle_locked_task(
                        session, repository, task_uuid, token, entity
                    )
        except _ReconcileAbort:
            return False

    def _is_still_unowned_active(self, entity: TaskRecord) -> bool:
        """Recheck the candidate predicates against a row already locked."""
        if entity.status.state not in ACTIVE_TASK_STATES:
            return False
        threshold = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(
            seconds=self._settings.orphan_task_age_seconds
        )
        return entity.updated_at is not None and entity.updated_at <= threshold

    async def _settle_locked_task(
        self,
        session,
        repository: TasksRepository,
        task_uuid: uuid.UUID,
        token: uuid.UUID,
        entity: TaskRecord,
    ) -> bool:
        """Write a fenced restart outcome and release the temporary lease.

        Args:
            entity: The task row as read by the caller's own locking query -
                never re-fetched here, since the caller already holds the row
                lock and has already paid for reading it once.

        Returns:
            ``True`` when an outcome was recorded for the task, ``False`` when
            only a stale lease had to go. Both commit.

        Raises:
            _ReconcileAbort: If the fencing write was refused, which rolls the
                whole candidate back rather than leaving a replaced token
                behind.
        """
        settled = settled_task(
            entity.to_task(str(task_uuid)), TaskSettlementReason.LEASE_EXPIRED
        )
        if settled is None:
            # A task that is terminal or waiting for input on purpose. The
            # lease is still stale and must not be rediscovered every pass,
            # but the task itself must remain untouched.
            await self._delete_claim(session, task_uuid, token)
            return False

        # The row is already locked by this transaction's own read above, so
        # the fenced upsert does not need to lock it a second time. agent_id
        # is carried over from the row just read, never reassigned here.
        if not await repository.save_owned_locked(
            TaskRecord.from_task(settled, entity.agent_id), token
        ):
            raise _ReconcileAbort
        await self._delete_claim(session, task_uuid, token)
        return True

    @staticmethod
    async def _delete_claim(session, task_uuid: uuid.UUID, token: uuid.UUID) -> None:
        """Delete only the temporary fencing incarnation used by settlement."""
        await TaskClaimsRepository(session).release(task_uuid, token)

"""Task claim repository implementation."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Optional, Type

from sqlalchemy import CTE, delete, exists, func, literal, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from aion.db.postgres.constants import TASK_EVENT_CHANNEL
from aion.db.postgres.events import TaskEvent, TaskEventKind
from aion.db.postgres.models import TaskClaimModel
from aion.db.postgres.records import TaskClaimRecord
from aion.db.postgres.repositories.base import BaseRepository

_TASK_CLAIM_COLUMNS = (
    TaskClaimModel.task_id,
    TaskClaimModel.agent_id,
    TaskClaimModel.owner_token,
    TaskClaimModel.lease_expires_at,
    TaskClaimModel.acquired_at,
    TaskClaimModel.renewed_at,
    TaskClaimModel.owner_instance_id,
    TaskClaimModel.cancel_requested_at,
)


class TaskClaimsRepository(BaseRepository[TaskClaimModel, TaskClaimRecord]):
    """Repository for the expiring execution leases in ``task_claims``.

    Acquire, renew and release are conditional writes fenced by an owner
    token, not plain CRUD, so each gets its own method with its own WHERE
    predicate rather than going through a generic ``save``. The table's
    primary key is ``task_id`` rather than ``id``, so the base class's
    id-keyed helpers (``find_by_id`` and friends) do not apply here; use
    :meth:`find_by_task_id` instead.
    """

    @property
    def model_class(self) -> Type[TaskClaimModel]:
        """SQLAlchemy ORM model for the task_claims table."""
        return TaskClaimModel

    @property
    def entity_class(self) -> Type[TaskClaimRecord]:
        """Pydantic domain entity used as the public return type."""
        return TaskClaimRecord

    @staticmethod
    def owned_cte(task_id: uuid.UUID, owner_token: uuid.UUID) -> CTE:
        """Build the fencing predicate as a CTE: one row while the token still owns the lease.

        Shared with :meth:`TasksRepository.save_owned` so the claim table and
        the task table agree on what "still owned" means from the same
        expression, rather than keeping two independent copies of the
        predicate in sync by hand.
        """
        return (
            select(TaskClaimModel.task_id)
            .where(
                TaskClaimModel.task_id == task_id,
                TaskClaimModel.owner_token == owner_token,
            )
            .cte("owned")
        )

    async def acquire(
        self,
        task_id: uuid.UUID,
        agent_id: str,
        owner_token: uuid.UUID,
        ttl: timedelta,
        owner_instance_id: Optional[str],
    ) -> Optional[TaskClaimRecord]:
        """Acquire a free or expired lease using a conditional upsert.

        Args:
            task_id: Identifier of the task being claimed.
            agent_id: Identity of the agent process making the claim.
            owner_token: Fresh incarnation token for this acquisition.
            ttl: How long the new lease survives without a renewal.
            owner_instance_id: Diagnostic pod identity to record on the row.

        Returns:
            The new claim, or ``None`` when a live lease already exists.
        """
        insert_stmt = pg_insert(TaskClaimModel).values(
            task_id=task_id,
            agent_id=agent_id,
            owner_token=owner_token,
            lease_expires_at=func.clock_timestamp() + ttl,
            owner_instance_id=owner_instance_id,
        )
        stmt = insert_stmt.on_conflict_do_update(
            index_elements=[TaskClaimModel.task_id],
            set_={
                "agent_id": insert_stmt.excluded.agent_id,
                "owner_token": insert_stmt.excluded.owner_token,
                "lease_expires_at": insert_stmt.excluded.lease_expires_at,
                "acquired_at": func.clock_timestamp(),
                "renewed_at": func.clock_timestamp(),
                "owner_instance_id": insert_stmt.excluded.owner_instance_id,
            },
            where=TaskClaimModel.lease_expires_at <= func.clock_timestamp(),
        ).returning(*_TASK_CLAIM_COLUMNS)
        result = await self._session.execute(stmt)
        row = result.mappings().first()
        return TaskClaimRecord(**row) if row is not None else None

    async def renew(
        self,
        task_id: uuid.UUID,
        owner_token: uuid.UUID,
        ttl: timedelta,
    ) -> Optional[TaskClaimRecord]:
        """Conditionally extend a lease still held by ``owner_token``.

        Returns:
            The renewed claim, or ``None`` when the token no longer owns it.
        """
        stmt = (
            update(TaskClaimModel)
            .where(
                TaskClaimModel.task_id == task_id,
                TaskClaimModel.owner_token == owner_token,
            )
            .values(
                lease_expires_at=func.clock_timestamp() + ttl,
                renewed_at=func.clock_timestamp(),
            )
            .returning(*_TASK_CLAIM_COLUMNS)
        )
        result = await self._session.execute(stmt)
        row = result.mappings().first()
        return TaskClaimRecord(**row) if row is not None else None

    async def renew_batch(
        self,
        claims: list[tuple[uuid.UUID, uuid.UUID]],
        ttl: timedelta,
    ) -> list[TaskClaimRecord]:
        """Conditionally extend many leases in one statement.

        Same fencing predicate as :meth:`renew`, applied to every
        ``(task_id, owner_token)`` pair at once: one heartbeat tick's
        renewals become one round trip instead of one per held claim. A pair
        whose token no longer owns the lease - or whose task_id has none -
        is simply absent from the result, exactly as a single ``renew``
        would return ``None`` for it.

        Args:
            claims: ``(task_id, owner_token)`` pairs to renew, one per
                locally held claim.
            ttl: How long each renewed lease survives from now.

        Returns:
            The renewed rows, in no particular order. Pairs not present here
            were not renewed.
        """
        if not claims:
            return []
        stmt = (
            update(TaskClaimModel)
            .where(tuple_(TaskClaimModel.task_id, TaskClaimModel.owner_token).in_(claims))
            .values(
                lease_expires_at=func.clock_timestamp() + ttl,
                renewed_at=func.clock_timestamp(),
            )
            .returning(*_TASK_CLAIM_COLUMNS)
        )
        result = await self._session.execute(stmt)
        return [TaskClaimRecord(**row) for row in result.mappings().all()]

    async def release(self, task_id: uuid.UUID, owner_token: uuid.UUID) -> None:
        """Conditionally delete a lease still held by ``owner_token``."""
        await self._session.execute(
            delete(TaskClaimModel).where(
                TaskClaimModel.task_id == task_id,
                TaskClaimModel.owner_token == owner_token,
            )
        )

    async def revoke_unconditionally(self, task_id: uuid.UUID, agent_id: str) -> None:
        """Delete any lease for a task, regardless of its owner token.

        This is intentionally stronger than :meth:`release`: a control-plane
        cancellation may arrive on a process that does not own the task, and
        removing the lease is how it fences the current owner from later
        writes.  Callers must first lock the corresponding task row and keep
        both operations in the same transaction. ``agent_id`` is checked
        too, so a task id that happens to collide across agents cannot
        revoke a claim that belongs to a different one.
        """
        await self._session.execute(
            delete(TaskClaimModel).where(
                TaskClaimModel.task_id == task_id,
                TaskClaimModel.agent_id == agent_id,
            )
        )

    async def request_cancel(self, task_id: uuid.UUID, agent_id: str) -> Optional[TaskClaimRecord]:
        """Mark a live claim as having a cancellation requested against it.

        This is the control-plane channel for a pod that does not hold the
        claim: it cannot run the owner's teardown itself, so it leaves a mark
        the owner discovers on its next heartbeat renewal - or sooner, over
        ``TASK_EVENT_CHANNEL``, see below - and acts on it locally.
        ``COALESCE`` makes a repeated request idempotent - it never pushes
        the timestamp forward, so a retried ``tasks/cancel`` does not reset
        whatever grace period is measured from it.

        A ``CANCEL_REQUESTED`` event is only emitted when this UPDATE
        actually touched a row - a repeated request that finds the mark
        already set re-runs the same WHERE and still returns that row, so
        gating strictly on "no prior mark" would silently stop notifying on
        a retry. Every live-claim outcome renotifies; only "no such claim"
        (``row is None``) skips it, since there is no owner left to wake.
        This trades one redundant heartbeat-cadence duplicate notification
        for never under-notifying a retried request.

        Returns:
            The updated claim, or ``None`` when no live claim exists for this
            task under this agent - the caller's signal that there is no
            owner to ask, and it must close the task out itself instead.
        """
        stmt = (
            update(TaskClaimModel)
            .where(
                TaskClaimModel.task_id == task_id,
                TaskClaimModel.agent_id == agent_id,
            )
            .values(
                cancel_requested_at=func.coalesce(
                    TaskClaimModel.cancel_requested_at, func.clock_timestamp()
                )
            )
            .returning(*_TASK_CLAIM_COLUMNS)
        )
        result = await self._session.execute(stmt)
        row = result.mappings().first()
        if row is None:
            return None
        await self._notify(TaskEventKind.CANCEL_REQUESTED, task_id)
        return TaskClaimRecord(**row)

    async def find_cancel_overdue(self, grace: timedelta) -> list[TaskClaimRecord]:
        """Read claims whose cancellation request has outlived its grace period.

        A claim can carry a pending cancel while its lease keeps renewing
        normally - the owner is alive but stuck unwinding. That claim never
        goes stale, so the ordinary expired-lease sweep never finds it; this
        is the reaper's separate pass for exactly that case, read through the
        partial index on ``cancel_requested_at``.

        Args:
            grace: How long a cancellation request may go unhonored before
                the reaper forces the task closed regardless of lease state.
        """
        stmt = select(*_TASK_CLAIM_COLUMNS).where(
            TaskClaimModel.cancel_requested_at.isnot(None),
            TaskClaimModel.cancel_requested_at <= func.clock_timestamp() - grace,
        )
        result = await self._session.execute(stmt)
        return [TaskClaimRecord(**row) for row in result.mappings().all()]

    async def notify_cancel_resolved(self, task_id: uuid.UUID) -> None:
        """Wake a pod waiting on this task's cancellation, if one is waiting.

        Call this in the same transaction as the terminal write it announces,
        before commit: ``NOTIFY`` only takes effect on commit, so a rollback
        of the terminal write silently takes the notification with it, and no
        separate rollback handling is needed here.

        The check - "does a live claim for this task still carry a
        cancellation request" - is what keeps this silent on every ordinary
        terminal write. Only a task whose claim was marked by
        :meth:`request_cancel` has anyone listening for it at all; every
        other terminal write pays for one indexed existence check and
        notifies nobody. This fires on any terminal outcome, not only
        ``CANCELED`` - a task that reached its own outcome in the race with
        the request still resolves the wait, just not with the outcome the
        requester asked for.
        """
        stmt = select(
            func.pg_notify(
                literal(TASK_EVENT_CHANNEL),
                literal(TaskEvent(kind=TaskEventKind.CANCEL_RESOLVED, task_id=str(task_id)).model_dump_json()),
            )
        ).where(
            exists(
                select(1).where(
                    TaskClaimModel.task_id == task_id,
                    TaskClaimModel.cancel_requested_at.isnot(None),
                )
            )
        )
        await self._session.execute(stmt)

    async def _notify(self, kind: TaskEventKind, task_id: uuid.UUID) -> None:
        """Emit one ``TASK_EVENT_CHANNEL`` notification, unconditionally.

        Unconditional on purpose: unlike :meth:`notify_cancel_resolved`, the
        one caller here (:meth:`request_cancel`) already only reaches this
        after confirming - via its own UPDATE's ``returning`` - that there is
        a live claim to notify about, so a second existence check would be
        redundant.
        """
        payload = TaskEvent(kind=kind, task_id=str(task_id)).model_dump_json()
        await self._session.execute(
            select(func.pg_notify(literal(TASK_EVENT_CHANNEL), literal(payload)))
        )

    async def find_by_task_id(self, task_id: uuid.UUID, agent_id: str) -> Optional[TaskClaimRecord]:
        """Read a claim row unconditionally, for diagnostics only.

        Not part of any fencing predicate: nothing here decides ownership,
        it only reports whatever the row currently says.
        """
        stmt = select(*_TASK_CLAIM_COLUMNS).where(
            TaskClaimModel.task_id == task_id,
            TaskClaimModel.agent_id == agent_id,
        )
        result = await self._session.execute(stmt)
        row = result.mappings().first()
        return TaskClaimRecord(**row) if row is not None else None

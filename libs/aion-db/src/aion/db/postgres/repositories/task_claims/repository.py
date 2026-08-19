"""Task claim repository implementation."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Optional, Type

from sqlalchemy import CTE, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from aion.db.postgres.models import TaskClaimModel
from aion.db.postgres.records import TaskClaimRecord
from aion.db.postgres.repositories.base import BaseRepository

_TASK_CLAIM_COLUMNS = (
    TaskClaimModel.task_id,
    TaskClaimModel.owner_token,
    TaskClaimModel.lease_expires_at,
    TaskClaimModel.acquired_at,
    TaskClaimModel.renewed_at,
    TaskClaimModel.owner_instance_id,
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
        owner_token: uuid.UUID,
        ttl: timedelta,
        owner_instance_id: Optional[str],
    ) -> Optional[TaskClaimRecord]:
        """Acquire a free or expired lease using a conditional upsert.

        Args:
            task_id: Identifier of the task being claimed.
            owner_token: Fresh incarnation token for this acquisition.
            ttl: How long the new lease survives without a renewal.
            owner_instance_id: Diagnostic pod identity to record on the row.

        Returns:
            The new claim, or ``None`` when a live lease already exists.
        """
        insert_stmt = pg_insert(TaskClaimModel).values(
            task_id=task_id,
            owner_token=owner_token,
            lease_expires_at=func.clock_timestamp() + ttl,
            owner_instance_id=owner_instance_id,
        )
        stmt = insert_stmt.on_conflict_do_update(
            index_elements=[TaskClaimModel.task_id],
            set_={
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

    async def release(self, task_id: uuid.UUID, owner_token: uuid.UUID) -> None:
        """Conditionally delete a lease still held by ``owner_token``."""
        await self._session.execute(
            delete(TaskClaimModel).where(
                TaskClaimModel.task_id == task_id,
                TaskClaimModel.owner_token == owner_token,
            )
        )

    async def revoke_unconditionally(self, task_id: uuid.UUID) -> None:
        """Delete any lease for a task, regardless of its owner token.

        This is intentionally stronger than :meth:`release`: a control-plane
        cancellation may arrive on a process that does not own the task, and
        removing the lease is how it fences the current owner from later
        writes.  Callers must first lock the corresponding task row and keep
        both operations in the same transaction.
        """
        await self._session.execute(
            delete(TaskClaimModel).where(TaskClaimModel.task_id == task_id)
        )

    async def find_by_task_id(self, task_id: uuid.UUID) -> Optional[TaskClaimRecord]:
        """Read a claim row unconditionally, for diagnostics only.

        Not part of any fencing predicate: nothing here decides ownership,
        it only reports whatever the row currently says.
        """
        stmt = select(*_TASK_CLAIM_COLUMNS).where(TaskClaimModel.task_id == task_id)
        result = await self._session.execute(stmt)
        row = result.mappings().first()
        return TaskClaimRecord(**row) if row is not None else None

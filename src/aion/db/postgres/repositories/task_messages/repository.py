"""Task history repository implementation."""

from __future__ import annotations

import uuid
from typing import Dict, List, Optional, Sequence, Type

from sqlalchemy import asc, desc, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from a2a.types import Message

from aion.db.postgres.models import TaskMessageModel
from aion.db.postgres.records import TaskMessageRecord
from aion.db.postgres.repositories.base import BaseRepository


class TaskMessagesRepository(BaseRepository[TaskMessageModel, TaskMessageRecord]):
    """Repository for a task's durable history, one row per message.

    ``Task.history`` only ever grows by appending — see
    ``a2a.server.tasks.task_manager.TaskManager``, which never removes or
    reorders an entry once written. That is what makes counting rows already
    stored, and inserting only the entries past that count, both correct and
    idempotent: a retried save recomputes the same count and finds nothing
    left to insert.
    """

    @property
    def model_class(self) -> Type[TaskMessageModel]:
        """SQLAlchemy ORM model for the task_messages table."""
        return TaskMessageModel

    @property
    def entity_class(self) -> Type[TaskMessageRecord]:
        """Pydantic domain entity used as the public return type."""
        return TaskMessageRecord

    async def append_new(self, task_id: uuid.UUID, history: Sequence[Message]) -> None:
        """Insert whatever prefix of ``history`` is not already stored.

        Args:
            task_id: Task the history belongs to.
            history: The caller's full, current ``Task.history`` - not a
                delta. Entries already persisted are skipped by position;
                only a genuinely new tail is written.
        """
        if not history:
            return

        stored = await self._session.scalar(
            select(func.count())
            .select_from(TaskMessageModel)
            .where(TaskMessageModel.task_id == task_id)
        )
        new_entries = list(history)[stored:]
        if not new_entries:
            return

        stmt = pg_insert(TaskMessageModel).values(
            [
                {
                    "task_id": task_id,
                    "seq": stored + offset,
                    "message_id": message.message_id or None,
                    "payload": message,
                }
                for offset, message in enumerate(new_entries)
            ]
        )
        # No index named: a race that lands the same message_id under a
        # different seq is just as much a duplicate as one that lands on the
        # same seq, and either unique constraint should suppress it the same
        # way.
        await self._session.execute(stmt.on_conflict_do_nothing())

    async def find_by_task_id(
        self, task_id: uuid.UUID, *, limit: Optional[int] = None
    ) -> List[Message]:
        """Return one task's history, in order.

        Args:
            task_id: Task to read.
            limit: When given, only the last ``limit`` entries - still
                returned in chronological order, not reversed.
        """
        stmt = select(TaskMessageModel.payload).where(TaskMessageModel.task_id == task_id)
        if limit is None:
            stmt = stmt.order_by(asc(TaskMessageModel.seq))
            result = await self._session.execute(stmt)
            return list(result.scalars().all())

        stmt = stmt.order_by(desc(TaskMessageModel.seq)).limit(limit)
        result = await self._session.execute(stmt)
        return list(reversed(result.scalars().all()))

    async def find_by_task_ids(
        self, task_ids: Sequence[uuid.UUID], *, limit: Optional[int] = None
    ) -> Dict[uuid.UUID, List[Message]]:
        """Return history for many tasks in one query, keyed by task id.

        The bulk counterpart of :meth:`find_by_task_id`, for hydrating a page
        of ``ListTasks`` results without one round trip per task. Every id in
        ``task_ids`` is a key in the result, even one with no history at all.
        """
        grouped: Dict[uuid.UUID, List[Message]] = {task_id: [] for task_id in task_ids}
        if not task_ids:
            return grouped

        if limit is None:
            stmt = (
                select(TaskMessageModel.task_id, TaskMessageModel.payload)
                .where(TaskMessageModel.task_id.in_(task_ids))
                .order_by(TaskMessageModel.task_id, asc(TaskMessageModel.seq))
            )
            result = await self._session.execute(stmt)
            for task_id, payload in result.all():
                grouped[task_id].append(payload)
            return grouped

        # A window function keeps this to one statement: rank each task's own
        # messages newest-first, keep only the top `limit` per task, then
        # re-sort that kept slice back into chronological order.
        row_number = (
            func.row_number()
            .over(
                partition_by=TaskMessageModel.task_id,
                order_by=desc(TaskMessageModel.seq),
            )
            .label("rn")
        )
        windowed = (
            select(TaskMessageModel.task_id, TaskMessageModel.seq, TaskMessageModel.payload, row_number)
            .where(TaskMessageModel.task_id.in_(task_ids))
            .subquery()
        )
        stmt = (
            select(windowed.c.task_id, windowed.c.payload)
            .where(windowed.c.rn <= limit)
            .order_by(windowed.c.task_id, asc(windowed.c.seq))
        )
        result = await self._session.execute(stmt)
        for task_id, payload in result.all():
            grouped[task_id].append(payload)
        return grouped

"""Postgres implementation of ``TaskStore``."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional, List

from a2a.server.context import ServerCallContext
from a2a.types import Task

from aion.db.postgres.manager import db_manager
from aion.db.postgres.types import Pagination, Sorting, SortKey
from aion.db.postgres.repositories import TasksRepository
from aion.db.postgres.records import TaskRecord
from .base_task_store import BaseTaskStore


class PostgresTaskStore(BaseTaskStore):
    """Store tasks in a Postgres database using repository pattern."""

    @staticmethod
    def _task_to_entity(task: Task, task_id: uuid.UUID = None) -> TaskRecord:
        """Convert Task to TaskRecord entity."""
        if task_id is None:
            try:
                task_id = uuid.UUID(task.id)
            except (ValueError, AttributeError):
                task_id = uuid.uuid4()

        now = datetime.now(tz=timezone.utc)

        return TaskRecord(
            id=task_id,
            context_id=task.context_id,
            status=task.status,
            artifacts=task.artifacts,
            history=task.history,
            task_metadata=task.metadata,
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _entity_to_task(task_id: str, entity: TaskRecord) -> Task:
        """Convert TaskRecord entity to Task."""
        return entity.to_task(task_id)

    async def save(
            self, task: Task, context: ServerCallContext | None = None
    ) -> None:
        """Save a task."""
        try:
            task_id = uuid.UUID(task.id)
        except (ValueError, AttributeError):
            task_id = uuid.uuid4()

        entity = self._task_to_entity(task, task_id)

        async with db_manager.get_session() as session:
            repository = TasksRepository(session)
            await repository.save(entity)
            await session.commit()

    async def get(
            self, task_id: str, context: ServerCallContext | None = None
    ) -> Task | None:
        """Get a task by ID."""
        try:
            task_uuid = uuid.UUID(task_id)
        except ValueError:
            return None

        async with db_manager.get_session() as session:
            repository = TasksRepository(session)
            entity = await repository.find_by_id(task_uuid)

            if not entity:
                return None

            return self._entity_to_task(task_id, entity)

    async def delete(
            self, task_id: str, context: ServerCallContext | None = None
    ) -> None:
        """Delete a task by ID."""
        try:
            task_uuid = uuid.UUID(task_id)
        except ValueError:
            return

        async with db_manager.get_session() as session:
            repository = TasksRepository(session)
            await repository.delete_by_id(task_uuid)
            await session.commit()

    async def get_by_context(
            self,
            context_id: str,
            limit: Optional[int] = None,
            offset: Optional[int] = None
    ) -> list[Task]:
        """Get all tasks for a given context."""
        async with db_manager.get_session() as session:
            repository = TasksRepository(session)
            entities = await repository.find(
                context_id=context_id,
                pagination=Pagination(limit=limit, offset=offset),
                sorting=Sorting(SortKey(column="created_at")),
            )
            return [self._entity_to_task(str(e.id), e) for e in entities]

    async def save_task(self, task: Task) -> None:
        """Backwards compatibility method."""
        await self.save(task)

    async def get_context_ids(
            self,
            offset: Optional[int] = None,
            limit: Optional[int] = None
    ) -> List[str]:
        """Retrieve unique context IDs with pagination support."""
        async with db_manager.get_session() as session:
            repository = TasksRepository(session)
            return await repository.find_unique_context_ids(pagination=Pagination(limit=limit, offset=offset))

    async def get_context_tasks(
            self,
            context_id: str,
            offset: Optional[int] = None,
            limit: Optional[int] = None
    ) -> List[Task]:
        """Retrieve tasks for a specific context with pagination support."""
        async with db_manager.get_session() as session:
            repository = TasksRepository(session)
            records = await repository.find(
                context_id=context_id,
                pagination=Pagination(limit=limit, offset=offset),
                sorting=Sorting(SortKey(column="created_at")),
            )
        return [self._entity_to_task(str(r.id), r) for r in records]

    async def get_context_last_task(self, context_id: str) -> Optional[Task]:
        """Retrieve the most recent task for a specific context."""
        try:
            tasks = await self.get_context_tasks(context_id=context_id, limit=1)
            return tasks[0]
        except Exception:
            return None

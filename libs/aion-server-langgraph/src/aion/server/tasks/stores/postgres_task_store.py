"""Postgres implementation of ``TaskStore``."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional, List

from aion.server.db.manager import db_manager
from aion.server.db.repositories import TasksRepository
from aion.server.types.entities import TaskRecord

from a2a.types import Task
from .base_task_store import BaseTaskStore


class PostgresTaskStore(BaseTaskStore):
    """Store tasks in a Postgres database using repository pattern."""

    def _task_to_entity(self, task: Task, task_id: uuid.UUID = None) -> TaskRecord:
        """Convert Task to TaskRecord entity."""
        if task_id is None:
            try:
                task_id = uuid.UUID(task.id)
            except (ValueError, AttributeError):
                task_id = uuid.uuid4()

        now = datetime.now(tz=timezone.utc)

        return TaskRecord(
            id=task_id,
            context_id=task.contextId,
            task=task,
            created_at=now,
            updated_at=now
        )

    def _entity_to_task(self, entity: TaskRecord) -> Task:
        """Convert TaskRecord entity to Task."""
        task_data = entity.task

        if isinstance(task_data, dict):
            return Task.model_validate(task_data)
        elif hasattr(task_data, 'model_validate'):
            return task_data
        else:
            return Task.model_validate(task_data)

    async def save(self, task: Task) -> None:
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

    async def get(self, task_id: str) -> Task | None:
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

            return self._entity_to_task(entity)

    async def delete(self, task_id: str) -> None:
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
            entities = await repository.find_by_context(
                context_id=context_id,
                limit=limit,
                offset=offset
            )
            return [self._entity_to_task(entity) for entity in entities]

    async def save_task(self, task: Task) -> None:
        """Backwards compatibility method."""
        await self.save(task)

    async def get_context_ids(
            self,
            offset: Optional[int] = None,
            limit: Optional[int] = None
    ) -> List[str]:
        context_ids = []
        async with db_manager.get_session() as session:
            repository = TasksRepository(session)
            context_ids = await repository.find_unique_context_ids(offset=offset, limit=limit)
        return context_ids

    async def get_context_tasks(
            self,
            context_id: str,
            offset: Optional[int] = None,
            limit: Optional[int] = None
    ) -> List[Task]:
        task_records = []
        async with db_manager.get_session() as session:
            repository = TasksRepository(session)
            task_records = await repository.find_by_context(
                context_id=context_id,
                offset=offset,
                limit=limit)
        tasks = [record.task for record in task_records]
        return tasks
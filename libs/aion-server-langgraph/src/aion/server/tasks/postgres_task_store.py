"""Postgres implementation of ``TaskStore``."""

from __future__ import annotations

import uuid
import datetime as _dt

from pydantic import BaseModel

from aion.server.db.manager import db_manager
from aion.server.db.repositories import TasksRepository
from aion.server.types.entities import TaskRecord

try:  # pragma: no cover - optional dependency
    from a2a.server.tasks import TaskStore
    from a2a.types import Task
except Exception:  # pragma: no cover - tests without a2a
    class Task(BaseModel):
        id: str
        contextId: str

    class TaskStore:  # type: ignore
        """Fallback ``TaskStore`` protocol."""

        async def save(self, task: Task) -> None:  # pragma: no cover
            raise NotImplementedError

        async def get(self, task_id: str) -> Task | None:  # pragma: no cover
            raise NotImplementedError

        async def delete(self, task_id: str) -> None:  # pragma: no cover
            raise NotImplementedError


class PostgresTaskStore(TaskStore):
    """Store tasks in a Postgres database using repository pattern."""

    def _task_to_entity(self, task: Task, task_id: uuid.UUID = None) -> TaskRecord:
        """Convert Task to TaskRecord entity."""
        if task_id is None:
            try:
                task_id = uuid.UUID(task.id)
            except (ValueError, AttributeError):
                task_id = uuid.uuid4()

        now = _dt.datetime.utcnow()

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

    async def get_by_context(self, context_id: str) -> list[Task]:
        """Get all tasks for a given context."""
        async with db_manager.get_session() as session:
            repository = TasksRepository(session)
            entities = await repository.find_by_context(context_id)

            return [self._entity_to_task(entity) for entity in entities]

    async def save_task(self, task: Task) -> None:
        """Backwards compatibility method."""
        await self.save(task)

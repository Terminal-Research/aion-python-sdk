"""Task repository implementation."""

from __future__ import annotations

import uuid
from typing import List, Optional, Type

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from .base import BaseRepository
from ..models import TaskRecordModel
from aion.server.types import TaskRecord


class TasksRepository(BaseRepository[TaskRecordModel, TaskRecord]):
    """Repository for Task operations using entities."""

    def __init__(self, session: AsyncSession):
        super().__init__(session)

    @property
    def model_class(self) -> Type[TaskRecordModel]:
        return TaskRecordModel

    @property
    def entity_class(self) -> Type[TaskRecord]:
        return TaskRecord

    async def save(self, entity: TaskRecord) -> None:
        """Save or update a task entity."""
        # Check if exists
        stmt = select(self.model_class).where(self.model_class.id == entity.id)
        result = await self._session.execute(stmt)
        existing_model = result.scalar_one_or_none()

        if existing_model:
            # Update existing
            existing_model.context_id = entity.context_id
            existing_model.task = entity.task
            # updated_at will be set automatically by SQLAlchemy
            model = existing_model
        else:
            # Create new
            model = self.model_class(
                id=entity.id,
                context_id=entity.context_id,
                task=entity.task,
            )
            self._session.add(model)

        await self._session.flush()

    async def find_by_id(self, task_id: uuid.UUID) -> Optional[TaskRecord]:
        """Find task by ID."""
        stmt = select(self.model_class).where(self.model_class.id == task_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()

        if not model:
            return None

        return self.entity_class.model_validate(model, from_attributes=True)

    async def find_by_context(self, context_id: str) -> List[TaskRecord]:
        """Find all tasks by context ID."""
        stmt = select(self.model_class).where(self.model_class.context_id == context_id)
        result = await self._session.execute(stmt)
        models = result.scalars().all()

        return [self.entity_class.model_validate(model, from_attributes=True) for model in models]

    async def delete_by_id(self, task_id: uuid.UUID) -> bool:
        """Delete task by ID."""
        stmt = delete(self.model_class).where(self.model_class.id == task_id)
        result = await self._session.execute(stmt)
        return result.rowcount > 0

    async def exists(self, task_id: uuid.UUID) -> bool:
        """Check if task exists."""
        stmt = select(self.model_class.id).where(self.model_class.id == task_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

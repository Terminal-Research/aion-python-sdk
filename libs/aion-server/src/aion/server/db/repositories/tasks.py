"""Task repository implementation."""

from __future__ import annotations

from typing import List, Type, Optional

from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from aion.server.types import TaskRecord
from .base import BaseRepository
from ..models import TaskRecordModel


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

    async def find_by_context(
            self,
            context_id: str,
            limit: Optional[int] = None,
            offset: Optional[int] = None,
            descending_order: bool = True
    ) -> List[TaskRecord]:
        """Find all tasks by context ID."""
        stmt = (
            select(self.model_class)
            .where(self.model_class.context_id == context_id))

        if descending_order:
            stmt = stmt.order_by(desc(self.model_class.created_at))
        if offset:
            stmt = stmt.offset(offset)
        if limit:
            stmt = stmt.limit(limit)

        return await self._execute_and_convert_multiple(stmt)

    async def find_unique_context_ids(
            self,
            limit: Optional[int] = None,
            offset: Optional[int] = None
    ) -> List[str]:
        """
        Find all unique context_id values ordered by latest task creation.

        Args:
            limit: Maximum number of context_ids to return
            offset: Number of context_ids to skip

        Returns:
            List of unique context_id strings ordered by latest created_at DESC
        """
        subquery = (
            select(
                self.model_class.context_id,
                func.max(self.model_class.created_at).label('latest_created_at')
            )
            .where(self.model_class.context_id.is_not(None))
            .group_by(self.model_class.context_id)
            .subquery()
        )

        stmt = (
            select(subquery.c.context_id)
            .order_by(desc(subquery.c.latest_created_at))
        )

        if offset:
            stmt = stmt.offset(offset)
        if limit:
            stmt = stmt.limit(limit)

        result = await self._session.execute(stmt)
        return [row[0] for row in result.fetchall()]

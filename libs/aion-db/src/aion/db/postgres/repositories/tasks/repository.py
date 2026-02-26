"""Task repository implementation."""

from __future__ import annotations

from typing import List, Type, Optional

from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

try:
    from a2a.types import Artifact
except Exception as exc:
    raise ImportError("The 'a2a-sdk' package is required to use this repository") from exc

from aion.db.postgres.records import TaskRecord
from aion.db.postgres.repositories.base import BaseRepository
from aion.db.postgres.models import TaskRecordModel
from aion.db.postgres.types import Pagination, Sorting
from aion.db.postgres.repositories.tasks.selectors import latest_artifacts, artifacts_by_version


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

    def _apply_filter(
            self,
            stmt,
            task_id: Optional[str] = None,
            context_id: Optional[str] = None,
    ):
        """Apply task filter conditions to ``stmt`` and return the updated statement."""
        if task_id is not None:
            stmt = stmt.where(self.model_class.id == task_id)
        if context_id is not None:
            stmt = stmt.where(self.model_class.context_id == context_id)
        return stmt

    async def save(self, entity: TaskRecord) -> None:
        """Save or update a task entity."""
        stmt = select(self.model_class).where(self.model_class.id == entity.id)
        result = await self._session.execute(stmt)
        existing_model = result.scalar_one_or_none()

        if existing_model:
            existing_model.context_id = entity.context_id
            existing_model.status = entity.status
            existing_model.artifacts = entity.artifacts
            existing_model.history = entity.history
            existing_model.task_metadata = entity.task_metadata
        else:
            model = self.model_class(
                id=entity.id,
                context_id=entity.context_id,
                status=entity.status,
                artifacts=entity.artifacts,
                history=entity.history,
                task_metadata=entity.task_metadata,
            )
            self._session.add(model)

        await self._session.flush()

    async def find(
            self,
            task_id: Optional[str] = None,
            context_id: Optional[str] = None,
            pagination: Optional[Pagination] = None,
            sorting: Optional[Sorting] = None,
    ) -> List[TaskRecord]:
        """Find tasks matching the given filter."""
        stmt = select(self.model_class)
        stmt = self._apply_filter(stmt, task_id=task_id, context_id=context_id)

        if sorting is not None:
            stmt = self._apply_sorting(stmt, sorting)
        if pagination is not None:
            stmt = self._apply_pagination(stmt, pagination)

        return await self._execute_and_convert_many(stmt)

    async def find_unique_context_ids(
            self,
            pagination: Optional[Pagination] = None,
    ) -> List[str]:
        """Find all unique context_id values ordered by latest task creation."""
        stmt = (
            select(self.model_class.context_id)
            .group_by(self.model_class.context_id)
            .order_by(desc(func.max(self.model_class.created_at)))
        )

        if pagination is not None:
            stmt = self._apply_pagination(stmt, pagination)

        result = await self._session.execute(stmt)
        return [row[0] for row in result.fetchall()]

    async def find_artifacts(
            self,
            task_id: Optional[str] = None,
            context_id: Optional[str] = None,
            artifact_name: Optional[str] = None,
            artifact_version: Optional[str] = None,
    ) -> List[Artifact]:
        """Find artifacts matching the given criteria.

        Either ``task_id`` or ``context_id`` must be provided to scope the search.

        ``artifact_version`` controls the search scope:
        - None → latest version of each artifact resolved by task creation order
        - specified → all artifacts within the scope matching ``artifact.metadata["version"]``

        ``artifact_name`` is an optional filter applied on top in both cases.
        """
        if task_id is None and context_id is None:
            raise ValueError("Either 'task_id' or 'context_id' must be provided.")

        stmt = (
            select(self.model_class.artifacts)
            .where(self.model_class.artifacts.isnot(None))
            .order_by(desc(self.model_class.created_at))
        )
        stmt = self._apply_filter(stmt, task_id=task_id, context_id=context_id)

        if artifact_name is not None:
            stmt = stmt.where(self.model_class.artifacts.contains([{"name": artifact_name}]))
        if artifact_version is not None:
            stmt = stmt.where(self.model_class.artifacts.contains([{"metadata": {"version": artifact_version}}]))

        result = await self._session.execute(stmt)
        rows = result.scalars().all()

        if artifact_version is not None:
            return artifacts_by_version(rows, artifact_version, artifact_name)

        return latest_artifacts(rows, artifact_name)

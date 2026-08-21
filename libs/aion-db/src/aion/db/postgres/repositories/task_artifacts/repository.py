"""Task artifact repository implementation."""

from __future__ import annotations

import uuid
from typing import Dict, List, Sequence, Type

from sqlalchemy import asc, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

try:
    from a2a.types import Artifact
except Exception as exc:
    raise ImportError("The 'a2a-sdk' package is required to use this repository") from exc

from aion.db.postgres.models import TaskArtifactModel
from aion.db.postgres.records import TaskArtifactRecord
from aion.db.postgres.repositories.base import BaseRepository


class TaskArtifactsRepository(BaseRepository[TaskArtifactModel, TaskArtifactRecord]):
    """Repository for a task's artifacts, one row per ``artifact_id``.

    ``a2a.server.tasks.task_manager.append_artifact_to_task`` already merges
    an artifact's chunks in memory - replacing it whole, or extending its
    parts - before a save is ever made, so what this repository ever sees is
    always an artifact's full current content. There is no append-in-SQL
    case to handle: every write is an upsert keyed by ``artifact_id``.
    """

    @property
    def model_class(self) -> Type[TaskArtifactModel]:
        """SQLAlchemy ORM model for the task_artifacts table."""
        return TaskArtifactModel

    @property
    def entity_class(self) -> Type[TaskArtifactRecord]:
        """Pydantic domain entity used as the public return type."""
        return TaskArtifactRecord

    async def upsert_batch(self, task_id: uuid.UUID, artifacts: Sequence[Artifact]) -> None:
        """Replace each artifact's row with its current content.

        Args:
            task_id: Task the artifacts belong to.
            artifacts: The caller's full, current ``Task.artifacts`` - every
                entry is upserted, since there is no cheaper way to tell
                "unchanged since last save" apart from "replaced" without
                comparing payloads.
        """
        if not artifacts:
            return

        stmt = pg_insert(TaskArtifactModel).values(
            [
                {
                    "task_id": task_id,
                    "artifact_id": artifact.artifact_id,
                    "payload": artifact,
                }
                for artifact in artifacts
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[TaskArtifactModel.task_id, TaskArtifactModel.artifact_id],
            set_={
                "payload": stmt.excluded.payload,
                "updated_at": func.clock_timestamp(),
            },
        )
        await self._session.execute(stmt)

    async def find_by_task_id(self, task_id: uuid.UUID) -> List[Artifact]:
        """Return one task's artifacts, in first-seen order."""
        stmt = (
            select(TaskArtifactModel.payload)
            .where(TaskArtifactModel.task_id == task_id)
            .order_by(asc(TaskArtifactModel.created_at), asc(TaskArtifactModel.artifact_id))
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def find_by_task_ids(
        self, task_ids: Sequence[uuid.UUID]
    ) -> Dict[uuid.UUID, List[Artifact]]:
        """Return artifacts for many tasks in one query, keyed by task id.

        The bulk counterpart of :meth:`find_by_task_id`, for hydrating a page
        of ``ListTasks`` results without one round trip per task. Every id in
        ``task_ids`` is a key in the result, even one with no artifacts.
        """
        grouped: Dict[uuid.UUID, List[Artifact]] = {task_id: [] for task_id in task_ids}
        if not task_ids:
            return grouped

        stmt = (
            select(TaskArtifactModel.task_id, TaskArtifactModel.payload)
            .where(TaskArtifactModel.task_id.in_(task_ids))
            .order_by(
                TaskArtifactModel.task_id,
                asc(TaskArtifactModel.created_at),
                asc(TaskArtifactModel.artifact_id),
            )
        )
        result = await self._session.execute(stmt)
        for task_id, payload in result.all():
            grouped[task_id].append(payload)
        return grouped

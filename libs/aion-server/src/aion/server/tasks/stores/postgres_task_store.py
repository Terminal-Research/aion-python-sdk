"""Postgres implementation of ``TaskStore``."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional, List

from a2a.server.context import ServerCallContext
from a2a.types import Task, TaskState
from a2a.types import a2a_pb2
from a2a.utils.constants import DEFAULT_LIST_TASKS_PAGE_SIZE
from a2a.utils.errors import InvalidParamsError
from a2a.utils.task import decode_page_token, encode_page_token

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
            task_metadata=task.metadata if task.HasField('metadata') else None,
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

    async def list(
            self,
            params: a2a_pb2.ListTasksRequest,
            context: ServerCallContext | None = None,
    ) -> a2a_pb2.ListTasksResponse:
        """List tasks with optional filtering and pagination."""
        status_state = TaskState.Name(params.status) if params.status else None
        status_timestamp_after = (
            params.status_timestamp_after.ToJsonString()
            if params.HasField('status_timestamp_after')
            else None
        )

        async with db_manager.get_session() as session:
            repository = TasksRepository(session)
            entities = await repository.find(
                context_id=params.context_id or None,
                status_state=status_state,
                status_timestamp_after=status_timestamp_after,
            )

        tasks = [self._entity_to_task(str(e.id), e) for e in entities]

        tasks.sort(
            key=lambda task: (
                task.HasField('status') and task.status.HasField('timestamp'),
                task.status.timestamp.ToJsonString()
                if task.HasField('status') and task.status.HasField('timestamp')
                else '',
                task.id,
            ),
            reverse=True,
        )

        total_size = len(tasks)
        start_idx = 0
        if params.page_token:
            start_task_id = decode_page_token(params.page_token)
            for i, task in enumerate(tasks):
                if task.id == start_task_id:
                    start_idx = i
                    break
            else:
                raise InvalidParamsError(f'Invalid page token: {params.page_token}')

        page_size = params.page_size or DEFAULT_LIST_TASKS_PAGE_SIZE
        end_idx = start_idx + page_size
        next_page_token = (
            encode_page_token(tasks[end_idx].id) if end_idx < total_size else None
        )
        tasks = tasks[start_idx:end_idx]

        response_kwargs: dict = dict(tasks=tasks, total_size=total_size, page_size=page_size)
        if next_page_token:
            response_kwargs['next_page_token'] = next_page_token
        return a2a_pb2.ListTasksResponse(**response_kwargs)

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

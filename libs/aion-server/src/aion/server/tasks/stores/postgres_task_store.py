"""Postgres implementation of ``TaskStore``."""

from __future__ import annotations

import uuid
from datetime import timezone
from typing import Optional, List

from a2a.server.context import ServerCallContext
from a2a.types import Task, TaskState
from a2a.types import a2a_pb2
from a2a.utils.constants import DEFAULT_LIST_TASKS_PAGE_SIZE
from a2a.utils.errors import InvalidParamsError
from a2a.utils.task import decode_page_token, encode_page_token

from aion.db.postgres.manager import db_manager
from aion.db.postgres.types import Pagination, Sorting, SortKey
from aion.db.postgres.repositories import STATUS_TIMESTAMP_SORT_KEY, TasksRepository
from aion.db.postgres.records import TaskRecord
from aion.server.a2a.constants import ACTIVE_TASK_STATES
from .base_task_store import BaseTaskStore


class PostgresTaskStore(BaseTaskStore):
    """Store tasks in a Postgres database using repository pattern."""

    @staticmethod
    def _require_task_uuid(task_id: str) -> uuid.UUID:
        """Parse a task identifier, refusing anything this store cannot address.

        Task rows are keyed by a UUID column, and every read path here
        (``get``, ``delete``) resolves an identifier by parsing it as a UUID.
        Substituting a generated UUID for an unparseable one would write the
        task under an identifier nobody holds: the write reports success, and
        every subsequent lookup by the caller's own identifier returns nothing.
        Failing loudly keeps that silent divergence out of the store.

        Args:
            task_id: Identifier carried by the task being written.

        Returns:
            The identifier parsed as a UUID.

        Raises:
            InvalidParamsError: If the identifier is missing or is not a UUID.
        """
        try:
            return uuid.UUID(task_id)
        except (ValueError, AttributeError, TypeError) as exc:
            raise InvalidParamsError(
                f'Task id must be a UUID for the Postgres task store, got: {task_id!r}'
            ) from exc

    @classmethod
    def _task_to_entity(cls, task: Task, task_id: uuid.UUID = None) -> TaskRecord:
        """Convert Task to TaskRecord entity."""
        if task_id is None:
            task_id = cls._require_task_uuid(task.id)

        return TaskRecord(
            id=task_id,
            context_id=task.context_id,
            status=task.status,
            artifacts=task.artifacts,
            history=task.history,
            task_metadata=task.metadata if task.HasField('metadata') else None,
        )

    @staticmethod
    def _entity_to_task(task_id: str, entity: TaskRecord) -> Task:
        """Convert TaskRecord entity to Task."""
        return entity.to_task(task_id)

    async def save(
            self, task: Task, context: ServerCallContext | None = None
    ) -> None:
        """Save a task.

        Raises:
            InvalidParamsError: If ``task.id`` is not a UUID (see
                :meth:`_require_task_uuid`).
        """
        entity = self._task_to_entity(task, self._require_task_uuid(task.id))

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
        """List tasks with optional filtering and pagination.

        Ordering and windowing are done by the database. Only the requested
        page is loaded as full task rows; the surrounding query reads
        identifiers alone, which is what makes the total size and the page
        token's position affordable on a large table.

        Args:
            params: Filter, page size, and page token of the request.
            context: Server call context (unused).

        Returns:
            The requested page, the total number of matching tasks, and a token
            for the next page when one exists.

        Raises:
            InvalidParamsError: If the page token does not name a task in the
                current result set.
        """
        status_state = TaskState.Name(params.status) if params.status else None
        status_timestamp_after = (
            params.status_timestamp_after.ToDatetime(tzinfo=timezone.utc)
            if params.HasField('status_timestamp_after')
            else None
        )
        filters = dict(
            context_id=params.context_id or None,
            status_state=status_state,
            status_timestamp_after=status_timestamp_after,
        )
        # Newest state change first; the id breaks ties so a page boundary
        # always falls in the same place for the same data.
        sorting = Sorting(
            SortKey(column=STATUS_TIMESTAMP_SORT_KEY, descending=True),
            SortKey(column="id", descending=True),
        )
        page_size = params.page_size or DEFAULT_LIST_TASKS_PAGE_SIZE

        async with db_manager.get_session() as session:
            repository = TasksRepository(session)
            ordered_ids = await repository.find_ids(sorting=sorting, **filters)

            total_size = len(ordered_ids)
            start_idx = 0
            if params.page_token:
                start_task_id = decode_page_token(params.page_token)
                try:
                    start_idx = ordered_ids.index(start_task_id)
                except ValueError:
                    raise InvalidParamsError(f'Invalid page token: {params.page_token}')

            entities = await repository.find(
                sorting=sorting,
                pagination=Pagination(offset=start_idx, limit=page_size),
                **filters,
            )

        tasks = [self._entity_to_task(str(e.id), e) for e in entities]

        end_idx = start_idx + page_size
        next_page_token = (
            encode_page_token(ordered_ids[end_idx]) if end_idx < total_size else None
        )

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

    async def get_active_tasks(self) -> List[Task]:
        """Retrieve every task in an active state, across all contexts.

        Queried one state at a time because the repository filters on a single
        state; the set is two members wide, and this runs once per process
        start, so the extra round trips cost nothing worth folding into a
        shared-package change.

        Returns:
            All tasks whose stored state is one of ``ACTIVE_TASK_STATES``.
        """
        records: List[TaskRecord] = []
        async with db_manager.get_session() as session:
            repository = TasksRepository(session)
            for state in ACTIVE_TASK_STATES:
                records.extend(await repository.find(status_state=TaskState.Name(state)))

        return [self._entity_to_task(str(r.id), r) for r in records]

    async def get_context_last_task(self, context_id: str) -> Optional[Task]:
        """Retrieve the most recent task for a specific context.

        Only an empty context yields ``None``. Database failures propagate:
        auto-discovery of a resumable task is built on this call, and a
        connection error reported as "no previous task" would silently start a
        fresh task over work that already exists.

        Args:
            context_id: Context whose most recent task is wanted.

        Returns:
            The most recently created task of the context, or ``None`` when the
            context holds no tasks.
        """
        tasks = await self.get_context_tasks(context_id=context_id, limit=1)
        return tasks[0] if tasks else None

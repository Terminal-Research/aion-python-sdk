"""Postgres implementation of ``TaskStore``."""

from __future__ import annotations

import uuid
from datetime import timezone
from typing import Optional, List

from a2a.server.context import ServerCallContext
from a2a.types import Task, TaskState
from a2a.types import a2a_pb2
from a2a.utils.constants import DEFAULT_LIST_TASKS_PAGE_SIZE, MAX_LIST_TASKS_PAGE_SIZE
from a2a.utils.errors import InvalidParamsError, TaskNotCancelableError
from a2a.utils.task import decode_page_token, encode_page_token

from aion.db.postgres.manager import db_manager
from aion.db.postgres.types import Pagination, Sorting, SortKey
from aion.db.postgres.repositories import (
    TaskClaimsRepository,
    TasksRepository,
)
from aion.db.postgres.records import TaskRecord
from aion.server.a2a.constants import ACTIVE_TASK_STATES, TERMINAL_TASK_STATES
from aion.server.tasks.identifiers import require_task_uuid
from aion.server.tasks.ownership import OwnershipProvider, TaskOwnershipLost
from .base_task_store import BaseTaskStore


class PostgresTaskStore(BaseTaskStore):
    """Store tasks in a Postgres database using repository pattern."""

    def __init__(self, ownership_provider: OwnershipProvider) -> None:
        """Initialize the store with its process-local ownership provider.

        The provider is required rather than optional: an instance without one
        would be a shared store whose writes are unfenced, which is the exact
        combination the pairing in ``StoreManager`` exists to make unreachable.
        """
        if ownership_provider is None:
            raise ValueError("PostgresTaskStore requires an ownership provider")
        self.ownership_provider = ownership_provider

    @staticmethod
    def _entity_to_task(task_id: str, entity: TaskRecord) -> Task:
        """Convert TaskRecord entity to Task."""
        return entity.to_task(task_id)

    async def save(
            self, task: Task, context: ServerCallContext | None = None
    ) -> None:
        """Save a task.

        Raises:
            ValueError: If ``task.id`` is not a UUID (see
                :meth:`TaskRecord.from_task`).
        """
        entity = TaskRecord.from_task(task)

        claim = self.ownership_provider.claim_for(task.id)
        if claim is None:
            raise TaskOwnershipLost(task.id)

        async with db_manager.get_session() as session:
            repository = TasksRepository(session)
            if not await repository.save_owned(entity, claim.owner_token):
                raise TaskOwnershipLost(task.id)
            await session.commit()

    async def cancel_with_ownership_revocation(
            self, task_id: str, context: ServerCallContext | None = None
    ) -> Task | None:
        """Cancel a task while holding its task-row mutex.

        Cancellation is the one control-plane path that intentionally does not
        present an owner token.  It locks ``tasks`` first, changes a non-terminal
        task to ``CANCELED``, and removes any claim in the same transaction so a
        running owner discovers the loss on its next heartbeat.

        The already-terminal case is reported from here rather than inferred by
        the caller from the returned state: a successful cancellation also ends
        in a terminal state, so afterwards the two are indistinguishable.

        Returns:
            The canceled task, or ``None`` when no such task exists.

        Raises:
            TaskNotCancelableError: If the task already has an outcome.
        """
        try:
            task_uuid = require_task_uuid(task_id)
        except InvalidParamsError:
            return None

        async with db_manager.get_session() as session:
            async with session.begin():
                tasks = TasksRepository(session)
                claims = TaskClaimsRepository(session)
                entity = await tasks.find_by_id_for_update(task_uuid)
                if entity is None:
                    return None

                task = self._entity_to_task(task_id, entity)
                if task.status.state in TERMINAL_TASK_STATES:
                    raise TaskNotCancelableError(
                        message=(
                            "Task cannot be canceled - current state: "
                            f"{TaskState.Name(task.status.state)}"
                        )
                    )

                task.status.state = TaskState.TASK_STATE_CANCELED
                await tasks.update_status(task_uuid, task.status)
                # Deleted without a token on purpose: removing the lease is how
                # a pod that is not the owner tells the owner it has stopped
                # being one.
                await claims.revoke_unconditionally(task_uuid)

                return task

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
        """List tasks with optional filtering and keyset pagination.

        ``total_size`` comes from a single ``COUNT(*)``, and a page is found
        by keeping only rows past the previous page's last position in the
        same order - never by loading every matching id or asking the
        database to skip ``OFFSET`` rows of a full sort. Both would cost the
        same as scanning the whole filtered result on a large table; this
        costs one page.

        The page token's wire format is unchanged - the id of the last row
        already returned - so an old token still decodes. What changed is
        that its position is looked up again on each request through
        :meth:`TasksRepository.sort_key_for_id` instead of being read off an
        in-memory list, which also means a token stays usable even if the
        task it names no longer matches the current filters.

        Args:
            params: Filter, page size, and page token of the request.
            context: Server call context (unused).

        Returns:
            The requested page, the total number of matching tasks, and a token
            for the next page when one exists.

        Raises:
            InvalidParamsError: If the page token does not name an existing
                task.
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
        page_size = params.page_size or DEFAULT_LIST_TASKS_PAGE_SIZE
        page_size = min(page_size, MAX_LIST_TASKS_PAGE_SIZE)

        async with db_manager.get_session() as session:
            repository = TasksRepository(session)

            total_size = await repository.count(**filters)

            after = None
            if params.page_token:
                start_task_id = decode_page_token(params.page_token)
                after = await repository.sort_key_for_id(start_task_id)
                if after is None:
                    raise InvalidParamsError(f'Invalid page token: {params.page_token}')

            # One extra row reveals whether a next page exists without a
            # second round trip or an approximate `has_more` from the count.
            entities = await repository.find_page(after=after, limit=page_size + 1, **filters)

        has_next = len(entities) > page_size
        entities = entities[:page_size]
        tasks = [self._entity_to_task(str(e.id), e) for e in entities]

        next_page_token = encode_page_token(str(entities[-1].id)) if has_next else None

        response_kwargs: dict = dict(tasks=tasks, total_size=total_size, page_size=page_size)
        if next_page_token:
            response_kwargs['next_page_token'] = next_page_token
        return a2a_pb2.ListTasksResponse(**response_kwargs)

    async def get_context_ids(
            self,
            offset: Optional[int] = None,
            limit: Optional[int] = None
    ) -> List[str]:
        """Retrieve unique context IDs, capped even when the caller asks for everything.

        ``limit=None`` used to reach the repository unbounded, and
        ``_apply_pagination`` skips a falsy limit - so an omitted limit read
        every context id the table has ever seen. Callers that truly want a
        default get one now instead of an unbounded scan.
        """
        limit = min(limit, MAX_LIST_TASKS_PAGE_SIZE) if limit else DEFAULT_LIST_TASKS_PAGE_SIZE
        async with db_manager.get_session() as session:
            repository = TasksRepository(session)
            return await repository.find_unique_context_ids(pagination=Pagination(limit=limit, offset=offset))

    async def get_context_tasks(
            self,
            context_id: str,
            offset: Optional[int] = None,
            limit: Optional[int] = None
    ) -> List[Task]:
        """Retrieve tasks for a specific context, capped even when the caller asks for everything.

        Same reasoning as :meth:`get_context_ids`: an omitted ``limit``
        previously reached the repository as ``None`` and came back
        unbounded, loading every task - full JSON artifacts and history
        included - that the context has ever had.
        """
        limit = min(limit, MAX_LIST_TASKS_PAGE_SIZE) if limit else DEFAULT_LIST_TASKS_PAGE_SIZE
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

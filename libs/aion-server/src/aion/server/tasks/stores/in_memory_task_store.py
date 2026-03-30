import asyncio
from a2a.server.context import ServerCallContext
from a2a.server.owner_resolver import OwnerResolver, resolve_user_scope
from a2a.types import a2a_pb2
from a2a.types.a2a_pb2 import Task
from a2a.utils.constants import DEFAULT_LIST_TASKS_PAGE_SIZE
from a2a.utils.errors import InvalidParamsError
from a2a.utils.task import decode_page_token, encode_page_token
from aion.shared.logging import get_logger
from typing import Iterator, Optional, List

from .base_task_store import BaseTaskStore

logger = get_logger()


class InMemoryTaskStore(BaseTaskStore):
    """In-memory implementation of TaskStore.

    Stores task objects in a nested dictionary keyed by owner then task_id.
    Task data is lost when the server process stops.
    """

    def __init__(
            self,
            owner_resolver: OwnerResolver = resolve_user_scope,
    ) -> None:
        logger.debug('Initializing InMemoryTaskStore')
        self.tasks: dict[str, dict[str, Task]] = {}
        self.lock = asyncio.Lock()
        self.owner_resolver = owner_resolver

    def _get_owner_tasks(self, owner: str) -> dict[str, Task]:
        return self.tasks.get(owner, {})

    def _all_tasks(self) -> Iterator[Task]:
        """Iterate over all tasks across all owners."""
        for owner_tasks in self.tasks.values():
            yield from owner_tasks.values()

    async def save(
            self, task: Task, context: ServerCallContext | None = None
    ) -> None:
        """Saves or updates a task in the in-memory store for the resolved owner."""
        owner = self.owner_resolver(context)
        if owner not in self.tasks:
            self.tasks[owner] = {}

        async with self.lock:
            self.tasks[owner][task.id] = task
            logger.debug(
                'Task %s for owner %s saved successfully.', task.id, owner
            )

    async def get(
            self, task_id: str, context: ServerCallContext | None = None
    ) -> Task | None:
        """Retrieves a task from the in-memory store by ID, for the given owner."""
        owner = self.owner_resolver(context)
        async with self.lock:
            logger.debug(
                'Attempting to get task with id: %s for owner: %s',
                task_id,
                owner,
            )
            owner_tasks = self._get_owner_tasks(owner)
            task = owner_tasks.get(task_id)
            if task:
                logger.debug(
                    'Task %s retrieved successfully for owner %s.',
                    task_id,
                    owner,
                )
                return task
            logger.debug(
                'Task %s not found in store for owner %s.', task_id, owner
            )
            return None

    async def list(
            self,
            params: a2a_pb2.ListTasksRequest,
            context: ServerCallContext | None = None,
    ) -> a2a_pb2.ListTasksResponse:
        """Retrieves a list of tasks from the store, for the given owner."""
        owner = self.owner_resolver(context)
        logger.debug('Listing tasks for owner %s with params %s', owner, params)

        async with self.lock:
            owner_tasks = self._get_owner_tasks(owner)
            tasks = list(owner_tasks.values())

        # Filter tasks
        if params.context_id:
            tasks = [
                task for task in tasks if task.context_id == params.context_id
            ]
        if params.status:
            tasks = [
                task for task in tasks if task.status.state == params.status
            ]
        if params.HasField('status_timestamp_after'):
            last_updated_after_iso = (
                params.status_timestamp_after.ToJsonString()
            )
            tasks = [
                task
                for task in tasks
                if (
                        task.HasField('status')
                        and task.status.HasField('timestamp')
                        and task.status.timestamp.ToJsonString()
                        >= last_updated_after_iso
                )
            ]

        # Order tasks by last update time. To ensure stable sorting, in cases where timestamps are null or not unique, do a second order comparison of IDs.
        tasks.sort(
            key=lambda task: (
                task.status.HasField('timestamp')
                if task.HasField('status')
                else False,
                task.status.timestamp.ToJsonString()
                if task.HasField('status') and task.status.HasField('timestamp')
                else '',
                task.id,
            ),
            reverse=True,
        )

        # Paginate tasks
        total_size = len(tasks)
        start_idx = 0
        if params.page_token:
            start_task_id = decode_page_token(params.page_token)
            valid_token = False
            for i, task in enumerate(tasks):
                if task.id == start_task_id:
                    start_idx = i
                    valid_token = True
                    break
            if not valid_token:
                raise InvalidParamsError(
                    f'Invalid page token: {params.page_token}'
                )
        page_size = params.page_size or DEFAULT_LIST_TASKS_PAGE_SIZE
        end_idx = start_idx + page_size
        next_page_token = (
            encode_page_token(tasks[end_idx].id)
            if end_idx < total_size
            else None
        )
        tasks = tasks[start_idx:end_idx]

        return a2a_pb2.ListTasksResponse(
            next_page_token=next_page_token,
            tasks=tasks,
            total_size=total_size,
            page_size=page_size,
        )

    async def delete(
            self, task_id: str, context: ServerCallContext | None = None
    ) -> None:
        """Deletes a task from the in-memory store by ID, for the given owner."""
        owner = self.owner_resolver(context)
        async with self.lock:
            logger.debug(
                'Attempting to delete task with id: %s for owner %s',
                task_id,
                owner,
            )

            owner_tasks = self._get_owner_tasks(owner)
            if task_id not in owner_tasks:
                logger.warning(
                    'Attempted to delete nonexistent task with id: %s for owner %s',
                    task_id,
                    owner,
                )
                return

            del owner_tasks[task_id]
            logger.debug(
                'Task %s deleted successfully for owner %s.', task_id, owner
            )
            if not owner_tasks:
                del self.tasks[owner]
                logger.debug('Removed empty owner %s from store.', owner)

    async def get_context_ids(
            self,
            offset: Optional[int] = None,
            limit: Optional[int] = None,
    ) -> List[str]:
        """Retrieve unique context IDs with pagination support."""
        offset = offset or 0
        context_ids = []
        seen = set()
        skipped = 0

        for task in reversed(list(self._all_tasks())):
            if not task.context_id:
                continue
            if task.context_id in seen:
                continue
            if skipped < offset:
                seen.add(task.context_id)
                skipped += 1
                continue
            context_ids.append(task.context_id)
            seen.add(task.context_id)
            if limit and len(context_ids) >= limit:
                break

        return context_ids

    async def get_context_tasks(
            self,
            context_id: str,
            offset: Optional[int] = None,
            limit: Optional[int] = None,
    ) -> List[Task]:
        """Retrieve tasks for a specific context with pagination support."""
        offset = offset or 0
        matching_tasks = []
        skipped = 0

        for task in reversed(list(self._all_tasks())):
            if task.context_id != context_id:
                continue
            if skipped < offset:
                skipped += 1
                continue
            matching_tasks.append(task)
            if limit and len(matching_tasks) >= limit:
                break

        return matching_tasks

    async def get_context_last_task(self, context_id: str) -> Optional[Task]:
        """Retrieve the most recent task for a specific context."""
        for task in reversed(list(self._all_tasks())):
            if task.context_id == context_id:
                return task
        return None

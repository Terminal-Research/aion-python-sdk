"""In-memory task store implementation for development and testing."""

import logging
import asyncio
from datetime import datetime, timezone
from a2a.server.context import ServerCallContext
from a2a.server.owner_resolver import OwnerResolver, resolve_user_scope
from a2a.types import TaskState, a2a_pb2
from a2a.types.a2a_pb2 import Task
from a2a.utils.constants import DEFAULT_LIST_TASKS_PAGE_SIZE
from a2a.utils.errors import InvalidParamsError, TaskNotCancelableError
from a2a.utils.task import decode_page_token, encode_page_token
from typing import NamedTuple, Optional, List

from aion.server.a2a.constants import TERMINAL_TASK_STATES
from aion.server.tasks.ownership import DegenerateOwnershipProvider
from .base_task_store import BaseTaskStore

logger = logging.getLogger(__name__)
_MIN_TIMESTAMP = datetime.min.replace(tzinfo=timezone.utc)


def _status_timestamp(task: Task) -> datetime | None:
    """Return a task timestamp as an aware datetime, if it has one."""
    if not task.HasField('status') or not task.status.HasField('timestamp'):
        return None
    return task.status.timestamp.ToDatetime(tzinfo=timezone.utc)


class _Stored(NamedTuple):
    owner: str
    task: Task


class InMemoryTaskStore(BaseTaskStore):
    """In-memory implementation of TaskStore.

    Stores every task once, keyed by task_id, with the owner its first write
    fixed. Task data is lost when the server process stops.

    The dictionary keeps creation order across owners: a task takes its
    place when it is first written, an update leaves it there, a delete
    removes it. Context listings read that order newest first, the order
    ``PostgresTaskStore`` gets from ``created_at``.

    Owner semantics match ``PostgresTaskStore`` exactly: a context limits
    every call to the owner this store's ``owner_resolver`` gives it, ``None``
    reaches every owner on reads, listings, cancel and delete, and a task's
    owner is fixed by its first write. See ``BaseTaskStore._owner_filter``
    and ``_owner_of_write``.
    """

    def __init__(
            self,
            owner_resolver: OwnerResolver = resolve_user_scope,
            *,
            guard_inline_files: bool = False,
    ) -> None:
        super().__init__(guard_inline_files=guard_inline_files)
        logger.debug('Initializing InMemoryTaskStore')
        self.tasks: dict[str, _Stored] = {}
        self.lock = asyncio.Lock()
        self.owner_resolver = owner_resolver
        self.ownership_provider = DegenerateOwnershipProvider()

    def _visible(self, owner: Optional[str]) -> list[Task]:
        """The tasks a call may see, oldest first: one owner's, or every owner's for None."""
        return [
            stored.task
            for stored in self.tasks.values()
            if owner is None or stored.owner == owner
        ]

    def _locate(self, task_id: str, owner: Optional[str]) -> Task | None:
        """The task, if it exists and the call may see it."""
        stored = self.tasks.get(task_id)
        if stored is None or (owner is not None and stored.owner != owner):
            return None
        return stored.task

    async def save(
            self, task: Task, context: ServerCallContext | None = None
    ) -> None:
        """Save a task; its owner is fixed by its first write.

        A new task takes the owner its context names - ``ServerCallContext()``
        the anonymous owner ``""`` - and ``None`` cannot create one. An
        existing task keeps its owner and its place in creation order.

        Raises:
            TaskOwnerUndefinedError: If the task is new and ``context`` is
                ``None``.
            TaskOwnerMismatchError: If ``context`` resolves to another owner.
        """
        task = self._persistable(task)
        write_owner = self._write_owner(context)

        async with self.lock:
            stored = self.tasks.get(task.id)
            owner = self._owner_of_write(
                task.id, write_owner, None if stored is None else stored.owner
            )
            # Assigning to an existing key keeps its position.
            self.tasks[task.id] = _Stored(owner, task)
            logger.debug(
                'Task %s for owner %s saved successfully.', task.id, owner
            )

    async def get(
            self, task_id: str, context: ServerCallContext | None = None
    ) -> Task | None:
        """Retrieves a task from the in-memory store by ID, for the given owner."""
        owner = self._owner_filter(context)
        async with self.lock:
            logger.debug(
                'Attempting to get task with id: %s for owner: %s',
                task_id,
                owner,
            )
            task = self._locate(task_id, owner)
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
        owner = self._owner_filter(context)
        logger.debug('Listing tasks for owner %s with params %s', owner, params)

        async with self.lock:
            tasks = self._visible(owner)

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
            last_updated_after = params.status_timestamp_after.ToDatetime(
                tzinfo=timezone.utc
            )
            tasks = [
                task
                for task in tasks
                if (
                    (timestamp := _status_timestamp(task)) is not None
                    and timestamp >= last_updated_after
                )
            ]

        # Order tasks by last update time. To ensure stable sorting, in cases where timestamps are null or not unique, do a second order comparison of IDs.
        tasks.sort(
            key=lambda task: (
                (timestamp := _status_timestamp(task)) is not None,
                timestamp or _MIN_TIMESTAMP,
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
        owner_filter = self._owner_filter(context)
        async with self.lock:
            logger.debug(
                'Attempting to delete task with id: %s for owner %s',
                task_id,
                owner_filter,
            )

            if self._locate(task_id, owner_filter) is None:
                logger.warning(
                    'Attempted to delete nonexistent task with id: %s for owner %s',
                    task_id,
                    owner_filter,
                )
                return

            del self.tasks[task_id]
            logger.debug('Task %s deleted successfully.', task_id)

    async def cancel_with_ownership_revocation(
            self, task_id: str, context: ServerCallContext | None = None
    ) -> Task | None:
        """Cancel a task in the local store without ownership state.

        Mirrors the durable store's contract, including reporting the
        already-terminal case as an error rather than leaving the caller to
        infer it from a state that a successful cancellation also produces.

        Returns:
            The canceled task, or ``None`` when no such task exists.

        Raises:
            TaskNotCancelableError: If the task already has an outcome.
        """
        owner = self._owner_filter(context)
        async with self.lock:
            task = self._locate(task_id, owner)
            if task is None:
                return None
            if task.status.state in TERMINAL_TASK_STATES:
                raise TaskNotCancelableError(
                    message=(
                        "Task cannot be canceled - current state: "
                        f"{TaskState.Name(task.status.state)}"
                    )
                )
            task.status.state = TaskState.TASK_STATE_CANCELED
            return task

    async def request_cancellation(
            self, task_id: str, context: ServerCallContext | None = None
    ) -> bool | None:
        """Report "no live claim" for any cancelable task.

        A single-process store has no distributed claim to mark: the
        in-process ``ActiveTaskRegistry`` already gives mutual exclusion
        stricter than a lease, so ``on_cancel_task`` finds and cancels the
        local execution directly and never needs this path to signal
        anything - see its first branch, tried before this one. When it is
        reached anyway, always answering ``False`` sends the caller straight
        to :meth:`cancel_with_ownership_revocation`, the correct single-writer
        outcome here.
        """
        owner = self._owner_filter(context)
        async with self.lock:
            task = self._locate(task_id, owner)
            if task is None:
                return None
            if task.status.state in TERMINAL_TASK_STATES:
                raise TaskNotCancelableError(
                    message=(
                        "Task cannot be canceled - current state: "
                        f"{TaskState.Name(task.status.state)}"
                    )
                )
            return False

    async def get_context_ids(
            self,
            offset: Optional[int] = None,
            limit: Optional[int] = None,
            context: ServerCallContext | None = None,
    ) -> List[str]:
        """Retrieve the resolved owner's unique context IDs."""
        owner = self._owner_filter(context)
        offset = offset or 0
        context_ids = []
        seen = set()
        skipped = 0

        async with self.lock:
            tasks = self._visible(owner)

        for task in reversed(tasks):
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
            context: ServerCallContext | None = None,
    ) -> List[Task]:
        """Retrieve the resolved owner's tasks for a specific context."""
        owner = self._owner_filter(context)
        offset = offset or 0
        matching_tasks = []
        skipped = 0

        async with self.lock:
            tasks = self._visible(owner)

        for task in reversed(tasks):
            if task.context_id != context_id:
                continue
            if skipped < offset:
                skipped += 1
                continue
            matching_tasks.append(task)
            if limit and len(matching_tasks) >= limit:
                break

        return matching_tasks

    async def get_context_last_task(
            self,
            context_id: str,
            context: ServerCallContext | None = None,
    ) -> Optional[Task]:
        """Retrieve the resolved owner's most recent task for a context."""
        tasks = await self.get_context_tasks(
            context_id=context_id,
            limit=1,
            context=context,
        )
        return tasks[0] if tasks else None

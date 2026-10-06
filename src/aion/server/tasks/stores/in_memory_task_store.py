"""In-memory task store implementation for development and testing."""

from __future__ import annotations

import logging
import asyncio
from datetime import datetime, timezone
from a2a.server.cluster.task_store import ConcurrentTaskModificationError
from a2a.server.context import ServerCallContext
from a2a.server.owner_resolver import OwnerResolver, resolve_user_scope
from a2a.types import a2a_pb2
from a2a.types.a2a_pb2 import Task
from a2a.utils.constants import DEFAULT_LIST_TASKS_PAGE_SIZE
from a2a.utils.errors import InvalidParamsError
from a2a.utils.task import (
    ListTasksCursor,
    decode_list_tasks_cursor,
    encode_list_tasks_cursor,
)
from typing import Callable, NamedTuple, Optional, List

from .base_task_store import BaseTaskStore

logger = logging.getLogger(__name__)


def _list_sort_key(task: Task) -> tuple[bool, int, str]:
    """The ``ListTasks`` order, as a2a-sdk sorts it: ``(has timestamp, timestamp, id)``, descending."""
    has_timestamp = task.HasField('status') and task.status.HasField('timestamp')
    return (
        has_timestamp,
        task.status.timestamp.ToNanoseconds() if has_timestamp else 0,
        task.id,
    )


def _cursor_for(task: Task) -> ListTasksCursor:
    """The cursor naming ``task``'s position in the ``ListTasks`` order."""
    has_timestamp, timestamp_ns, task_id = _list_sort_key(task)
    return ListTasksCursor(timestamp_ns=timestamp_ns if has_timestamp else None, task_id=task_id)


class _Stored(NamedTuple):
    owner: str
    task: Task
    updated_at: datetime


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

    Tasks removed by a context deletion are remembered as retired: a late
    write of one is refused with ``ConcurrentTaskModificationError``, the
    error a2a-sdk's task manager handles for a write another writer has
    overtaken, so it cannot bring back history the deletion removed. A new
    task is refused the same way when ``admits_new_task`` says its context is
    closed - the context catalog installs that check, so a message admitted
    before a deletion started cannot create a task after it, exactly as
    ``PostgresTaskStore.write`` refuses it.
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
        self._retired: set[str] = set()
        self.admits_new_task: Callable[[str], bool] = lambda context_id: True
        self.owner_resolver = owner_resolver

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
            ConcurrentTaskModificationError: If the task was removed by a
                context deletion, or is new and its context is being deleted.
        """
        task = self._persistable(task)
        write_owner = self._write_owner(context)

        async with self.lock:
            if task.id in self._retired:
                raise ConcurrentTaskModificationError(task.id)
            stored = self.tasks.get(task.id)
            if stored is None and task.context_id and not self.admits_new_task(task.context_id):
                raise ConcurrentTaskModificationError(task.id)
            owner = self._owner_of_write(
                task.id, write_owner, None if stored is None else stored.owner
            )
            # Assigning to an existing key keeps its position.
            self.tasks[task.id] = _Stored(owner, task, datetime.now(timezone.utc))
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
            last_updated_after_ns = params.status_timestamp_after.ToNanoseconds()
            tasks = [
                task
                for task in tasks
                if (
                    task.HasField('status')
                    and task.status.HasField('timestamp')
                    and task.status.timestamp.ToNanoseconds() >= last_updated_after_ns
                )
            ]

        # Newest status first; a task without a timestamp sorts last, and the
        # id breaks ties, so the order is total.
        tasks.sort(key=_list_sort_key, reverse=True)

        # The page token is a2a-sdk's keyset cursor: it carries the position
        # of the last task returned, so it stays valid if that task is updated
        # or deleted. A task updated mid-listing can move above the cursor and
        # be skipped.
        total_size = len(tasks)
        start_idx = 0
        if params.page_token:
            cursor = decode_list_tasks_cursor(params.page_token)
            if cursor is None:
                raise InvalidParamsError(
                    f'Invalid page token: {params.page_token}'
                )
            after = cursor.sort_key()
            start_idx = next(
                (i for i, task in enumerate(tasks) if _list_sort_key(task) < after),
                total_size,
            )
        page_size = params.page_size or DEFAULT_LIST_TASKS_PAGE_SIZE
        end_idx = start_idx + page_size
        next_page_token = (
            encode_list_tasks_cursor(_cursor_for(tasks[end_idx - 1]))
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

    async def tasks_in_context(self, context_id: str) -> list[tuple[Task, datetime]]:
        """Every owner's tasks of a context with their last write time, oldest first."""
        async with self.lock:
            return [
                (stored.task, stored.updated_at)
                for stored in self.tasks.values()
                if stored.task.context_id == context_id
            ]

    async def retire_context(self, context_id: str) -> list[str]:
        """Remove every task of a context and refuse any later write of them.

        Returns:
            The IDs of the removed tasks.
        """
        async with self.lock:
            retired = [
                task_id
                for task_id, stored in self.tasks.items()
                if stored.task.context_id == context_id
            ]
            for task_id in retired:
                del self.tasks[task_id]
            self._retired.update(retired)
            return retired

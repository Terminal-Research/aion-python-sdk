import asyncio
import logging
from typing import Optional, List

from a2a.server.context import ServerCallContext
from a2a.types import Task

from .base_task_store import BaseTaskStore

logger = logging.getLogger(__name__)


class InMemoryTaskStore(BaseTaskStore):
    """In-memory implementation of TaskStore.

    Stores task objects in a dictionary in memory. Task data is lost when the
    server process stops.
    """

    def __init__(self) -> None:
        """Initializes the InMemoryTaskStore."""
        logger.debug('Initializing InMemoryTaskStore')
        self.tasks: dict[str, Task] = {}
        self.lock = asyncio.Lock()

    async def save(
            self, task: Task, context: ServerCallContext | None = None
    ) -> None:
        """Saves or updates a task in the in-memory store."""
        async with self.lock:
            self.tasks[task.id] = task
            logger.debug('Task %s saved successfully.', task.id)

    async def get(
            self, task_id: str, context: ServerCallContext | None = None
    ) -> Task | None:
        """Retrieves a task from the in-memory store by ID."""
        async with self.lock:
            logger.debug('Attempting to get task with id: %s', task_id)
            task = self.tasks.get(task_id)
            if task:
                logger.debug('Task %s retrieved successfully.', task_id)
            else:
                logger.debug('Task %s not found in store.', task_id)
            return task

    async def delete(
            self, task_id: str, context: ServerCallContext | None = None
    ) -> None:
        """Deletes a task from the in-memory store by ID."""
        async with self.lock:
            logger.debug('Attempting to delete task with id: %s', task_id)
            if task_id in self.tasks:
                del self.tasks[task_id]
                logger.debug('Task %s deleted successfully.', task_id)
            else:
                logger.warning(
                    'Attempted to delete nonexistent task with id: %s', task_id
                )

    async def get_context_ids(
            self,
            offset: Optional[int] = None,
            limit: Optional[int] = None
    ) -> List[str]:
        """Retrieve unique context IDs with pagination support."""
        offset = offset or 0
        if offset >= len(self.tasks):
            return []

        context_ids = []
        seen = set()
        skipped = 0

        for task in reversed(list(self.tasks.values())):
            if task.context_id is None:
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
            limit: Optional[int] = None
    ) -> List[Task]:
        """Retrieve tasks for a specific context with pagination support."""
        offset = offset or 0

        matching_tasks = []
        skipped = 0

        for task in reversed(list(self.tasks.values())):
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
        result = None
        for task in reversed(list(self.tasks.values())):
            if task.context_id != context_id:
                continue

            result = task
            break

        return result

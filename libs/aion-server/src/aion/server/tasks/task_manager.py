from a2a.server.events import Event
from a2a.server.tasks import TaskManager
from a2a.types import Task, TaskArtifactUpdateEvent
from aion.shared.logging import get_logger
from aion.shared.types import MessageType

from aion.server.tasks import store_manager
from aion.server.utils import check_if_task_is_interrupted

logger = get_logger()


class AionTaskManager(TaskManager):
    """
    Extended task manager.

    Inherits from the base TaskManager and adds capabilities for automatically finding and assigning
    the last task from a given context, with optional filtering for interrupted tasks only.
    """

    async def process(self, event: Event) -> Event:
        """Processes an event, updates the task state if applicable, stores it, and returns the event.

        If the event is task-related (`Task`, `TaskStatusUpdateEvent`, `TaskArtifactUpdateEvent`),
        the internal task state is updated and persisted.

        Args:
            event: The event object received from the agent.

        Returns:
            The same event object that was processed (or skipped).
        """
        if self._check_process_skip_event(event):
            return event

        return await super().process(event)

    @staticmethod
    def _check_process_skip_event(event: Event) -> bool:
        """Checks if an event should be skipped from processing and storage.

        Stream delta artifacts are filtered out to prevent persisting intermediate
        streaming chunks, keeping only final/complete artifacts in storage.

        Args:
            event: The event to check.

        Returns:
            True if the event should be skipped, False otherwise.
        """
        if isinstance(event, TaskArtifactUpdateEvent):
            if event.artifact.artifact_id == MessageType.STREAM_DELTA.value:
                return True
        return False

    async def auto_discover_and_assign_task(self, interrupted: bool = False) -> Task | None:
        """
        Automatically discovers and assigns the last task from the current context.

        This method retrieves the most recent task associated with the current context
        and assigns it to this task manager instance. It can optionally filter to only
        assign interrupted tasks.
        """
        if self.task_id:
            logger.warning("Task ID already assigned, ignoring")
            return None

        last_task = await store_manager.get_store().get_context_last_task(context_id=self.context_id)
        if last_task is None:
            return None

        if interrupted and not check_if_task_is_interrupted(last_task):
            return None

        self.task_id = last_task.id
        self._current_task = last_task
        return last_task

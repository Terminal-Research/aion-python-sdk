import logging

from a2a.server.tasks import TaskManager
from a2a.types import Task

from aion.server.tasks import store_manager
from aion.server.utils import check_if_task_is_interrupted

logger = logging.getLogger(__name__)


class AionTaskManager(TaskManager):
    """
    Extended task manager.

    Inherits from the base TaskManager and adds capabilities for automatically finding and assigning
    the last task from a given context, with optional filtering for interrupted tasks only.
    """

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

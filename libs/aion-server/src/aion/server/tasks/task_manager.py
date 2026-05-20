from a2a.server.events import Event
from a2a.server.tasks import TaskManager
from a2a.types import Message, Task, TaskArtifactUpdateEvent, TaskState, TaskStatus, TaskStatusUpdateEvent
from aion.server.a2a.constants import TRANSIENT_ARTIFACT_IDS, NON_ACTIVE_TASK_STATES
from aion.server.a2a.utils import is_task_interrupted, task_history_message_ids, is_message_in_task_history
from aion.server.agent.execution.scope import AgentExecutionScopeHelper
from aion.core.logging import get_logger
from typing import override

from aion.server.tasks import store_manager

logger = get_logger()

_HISTORY_TERMINAL_STATES = NON_ACTIVE_TASK_STATES - {TaskState.TASK_STATE_COMPLETED}


class AionTaskManager(TaskManager):
    """
    Extended task manager.

    Inherits from the base TaskManager and adds capabilities for automatically finding and assigning
    the last task from a given context, with optional filtering for interrupted tasks only.
    """

    @override
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

        if isinstance(event, Message):
            event = await self._wrap_message_as_status_event(event)

        # The base class unconditionally moves task.status.message to history before
        # overwriting it. If _append_terminal_message_to_history already committed that
        # message (e.g. on INPUT_REQUIRED), clear it first to avoid a duplicate when
        # a subsequent event (e.g. TASK_STATE_CANCELED) is processed.
        if isinstance(event, (TaskStatusUpdateEvent, Task)):
            task = self._current_task
            if task is not None and task.status.HasField('message'):
                if is_message_in_task_history(task, message=task.status.message):
                    task.status.ClearField('message')

        result = await super().process(event)
        self._track_task_status(event)

        if (
                isinstance(event, (TaskStatusUpdateEvent, Task))
                and event.status.state in _HISTORY_TERMINAL_STATES
                and event.status.HasField('message')
        ):
            await self._append_terminal_message_to_history(event.status.message)

        return result

    @override
    def update_with_message(self, message: Message, task: Task) -> Task:
        """Override to prevent duplicate history entries when resuming an interrupted task.

        The base implementation unconditionally moves task.status.message to history.
        When _append_terminal_message_to_history has already committed the interrupt
        message to history, this would create a duplicate on resume.
        """
        if task.status.HasField('message'):
            if not is_message_in_task_history(task, message=task.status.message):
                task.history.append(task.status.message)
            task.status.ClearField('message')
        task.history.append(message)
        self._current_task = task
        return task

    async def _append_terminal_message_to_history(self, message: Message) -> None:
        """Add terminal status message to task history if not already present.

        The base TaskManager only moves the *previous* status.message to history
        before overwriting it. For non-completed terminal states the incoming
        message stays only in task.status — this method persists it to history
        so all three invocation paths (blocking, non-blocking, streaming) expose it.
        """
        task = self._current_task
        if task is None:
            return

        if is_message_in_task_history(task, message=message):
            return

        task.history.append(message)
        await self.task_store.save(task, self._call_context)

    async def _wrap_message_as_status_event(self, message: Message) -> TaskStatusUpdateEvent:
        """Wrap a standalone Message into a TaskStatusUpdateEvent.

        The base TaskManager does not persist raw Message objects. Wrapping the
        message in a working-state status event ensures it is saved to history
        via the standard status-update chain.
        """
        current_task = await self.get_task()
        state = current_task.status.state if current_task else TaskState.TASK_STATE_WORKING
        return TaskStatusUpdateEvent(
            task_id=self.task_id,
            context_id=self.context_id,
            status=TaskStatus(state=state, message=message),
        )

    @staticmethod
    def _track_task_status(event: Event) -> None:
        """Update task status in ExecutionScope when a TaskStatusUpdateEvent is received."""
        if not isinstance(event, TaskStatusUpdateEvent):
            return

        state = event.status.state
        AgentExecutionScopeHelper.set_task_status(state.value if hasattr(state, 'value') else state)

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
            if event.artifact.artifact_id in TRANSIENT_ARTIFACT_IDS:
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

        if interrupted and not is_task_interrupted(last_task):
            return None

        self.task_id = last_task.id
        self._current_task = last_task
        return last_task

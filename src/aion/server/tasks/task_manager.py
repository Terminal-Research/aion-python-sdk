"""Aion-specific task manager that orchestrates event routing and persistence."""

import logging
from collections.abc import Callable

from a2a.server.events import Event
from a2a.server.tasks import TaskManager
from a2a.types import Message, Task, TaskArtifactUpdateEvent, TaskState, TaskStatus, TaskStatusUpdateEvent
from aion.server.a2a.constants import (
    INTERRUPT_TASK_STATES,
    NON_ACTIVE_TASK_STATES,
    TRANSIENT_ARTIFACT_IDS,
)
from aion.server.a2a.utils import is_ephemeral_status_event, is_task_interrupted
from aion.server.agent.execution.scope import set_task_status
from typing import override

from aion.server.tasks import store_manager

logger = logging.getLogger(__name__)


class AionTaskManager(TaskManager):
    """
    Extended task manager.

    Inherits from the base TaskManager and adds capabilities for automatically finding and assigning
    the last task from a given context, with optional filtering for interrupted tasks only.

    Message placement follows the A2A convention implemented by the base class:
    `status.message` holds the most recent message and `history` holds everything
    before it, so the two together are the conversation with no duplicates.
    """

    def __init__(
        self,
        *args,
        on_interrupted: Callable[[str], None] | None = None,
        ownership_provider=None,
        **kwargs,
    ):
        """Initialize the manager and bind the registry's interrupt callback.

        The callback is deliberately synchronous and only schedules cleanup.
        ``save_task_event`` runs inside the SDK consumer; awaiting ``aclose``
        from that stack would cancel the consumer while it is handling the very
        event that requested cleanup.
        """
        super().__init__(*args, **kwargs)
        self._on_interrupted = on_interrupted
        self._ownership_provider = ownership_provider

    async def refresh_task(self) -> Task | None:
        """Reload the task snapshot from the store and replace the local cache.

        Ownership acquisition is a transition in the database.  A cached task
        from before that transition may already be terminal, so every execute
        path explicitly refreshes before it starts an ``ActiveTask``.
        """
        if not self.task_id:
            self._current_task = None
            return None
        self._current_task = await self.task_store.get(
            self.task_id,
            self._call_context,
        )
        return self._current_task

    @override
    async def _save_task(self, task: Task) -> None:
        """Persist through the store, then release non-active ownership.

        The store performs the fencing write.  Release happens only after that
        write returns successfully, so a terminal outcome never discards the
        token before it has served as proof of ownership. The receipt is
        captured before the write: a replacement incarnation must not be
        released if the old write races with ownership loss.
        """
        claim = (
            self._ownership_provider.claim_for(task.id)
            if self._ownership_provider is not None
            else None
        )
        await super()._save_task(task)

        state = task.status.state
        if state not in NON_ACTIVE_TASK_STATES:
            return

        # A claim only exists when a provider handed one out.
        if claim is not None:
            await self._ownership_provider.release(claim)

        if state in INTERRUPT_TASK_STATES and self._on_interrupted is not None:
            self._on_interrupted(task.id)

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

        event = await self._carry_pending_message(event)

        result = await super().process(event)
        self._track_task_status(event)
        return result

    async def _carry_pending_message(self, event: Event) -> Event:
        """Keep the turn's closing message in status when the task stops.

        The base class always demotes the pending `status.message` to history
        before applying a new status. That is right while the task is running —
        the message is no longer the current one — but wrong for the event that
        ends the turn: agents usually announce completion with a bare status,
        which would bury the answer in history and lose it entirely for a
        `historyLength=0` request.

        So when a non-active state arrives without a message of its own, the
        pending message is moved onto that event: it stays the current message,
        exactly once, and migrates to history on the next turn.
        """
        if not isinstance(event, TaskStatusUpdateEvent):
            return event

        if event.status.state not in NON_ACTIVE_TASK_STATES or event.status.HasField('message'):
            return event

        task = await self.get_task()
        if task is None or not task.status.HasField('message'):
            return event

        carried = TaskStatusUpdateEvent()
        carried.CopyFrom(event)
        carried.status.message.CopyFrom(task.status.message)
        task.status.ClearField('message')
        return carried

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
        set_task_status(state.value if hasattr(state, 'value') else state)

    @staticmethod
    def _check_process_skip_event(event: Event) -> bool:
        """Checks if an event should be skipped from processing and storage.

        Stream delta artifacts are filtered out to prevent persisting intermediate
        streaming chunks, keeping only final/complete artifacts in storage.

        Status updates flagged ephemeral are skipped the same way: the client
        still receives them off the event stream (this method only gates
        persistence), but they never become task.status.message and so are never
        folded into history — the milestone-only history policy for live
        progress (running commands, intermediate agent messages).

        Args:
            event: The event to check.

        Returns:
            True if the event should be skipped, False otherwise.
        """
        if isinstance(event, TaskArtifactUpdateEvent):
            if event.artifact.artifact_id in TRANSIENT_ARTIFACT_IDS:
                return True
        if is_ephemeral_status_event(event):
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
        if not self._call_context.user.is_authenticated:
            return None

        last_task = await store_manager.get_store().get_context_last_task(
            context_id=self.context_id,
            context=self._call_context,
        )
        if last_task is None:
            return None

        if interrupted and not is_task_interrupted(last_task):
            return None

        self.task_id = last_task.id
        self._current_task = last_task
        return last_task

"""Per-task push-notification decorator: durable events only, outcomes as the full Task."""

import logging
from a2a.server.tasks.push_notification_sender import PushNotificationEvent, PushNotificationSender
from a2a.types import Task, TaskState, TaskStatusUpdateEvent

from aion.server.a2a.constants import NON_ACTIVE_TASK_STATES
from aion.server.a2a.utils import is_ephemeral_event
from aion.server.tasks.protocols import AionTaskManagerProtocol

logger = logging.getLogger(__name__)


class TerminalTaskPushSender(PushNotificationSender):
    """Shapes the push channel of one task: what the task stores, and nothing else.

    The event consumer dispatches every event it handles to the webhooks (see
    ``EventConsumer._update_task_state`` in a2a-sdk), so this wrapper decides
    what a receiver gets:

    * An ephemeral event - a stream delta, an ephemeral message or reaction,
      a status update flagged ephemeral - is not sent. The task manager does
      not store it, and the push channel carries exactly what the task
      stores; ``is_ephemeral_event`` is the one predicate both go by.
    * A non-active status update is replaced by the Task snapshot, mirroring
      ``TerminalTaskProjection``: a task leaves the active states through
      exactly one event - the full Task.
    * Every other event is sent as it is.

    An outcome can also be written without an event the consumer pushes: when
    the agent raises, a2a-sdk's producer writes ``FAILED`` into the store
    directly. ``push_unsent_outcome`` sends that one once the execution is over.

    The snapshot is read through the task's own manager rather than the task
    store: push dispatch runs in the background consumer, which has no request
    context, and the manager carries the call context the store is scoped by.
    That makes the wrapper per-task — it is bound by the registry that creates
    the ActiveTask, not installed globally on the handler.
    """

    def __init__(self, inner: PushNotificationSender, task_manager: AionTaskManagerProtocol) -> None:
        self._inner = inner
        self._task_manager = task_manager
        # The non-active state of the last outcome delivered through this
        # wrapper, compared with the manager's ``written_outcome``.
        self._pushed_outcome: TaskState | None = None

    async def send_notification(self, task_id: str, event: PushNotificationEvent) -> None:
        if is_ephemeral_event(event):
            return

        if not self._is_non_active_status(event):
            if isinstance(event, Task) and event.status.state in NON_ACTIVE_TASK_STATES:
                self._pushed_outcome = event.status.state
            await self._inner.send_notification(task_id, event)
            return

        self._pushed_outcome = event.status.state
        task = await self._task_manager.get_task()
        if task is None:
            # A non-active transition is persisted before dispatch, so a miss
            # means the store is inconsistent. Deliver the raw event rather
            # than leave the client without an outcome.
            logger.error("Push for task %s: final Task missing from the store", task_id)
            await self._inner.send_notification(task_id, event)
            return

        if task.status.state not in NON_ACTIVE_TASK_STATES:
            # Defensive: the snapshot lags the event. Settle a copy with the
            # status the event announced, mirroring TerminalTaskProjection.
            settled = Task()
            settled.CopyFrom(task)
            settled.status.CopyFrom(event.status)
            task = settled

        await self._inner.send_notification(task_id, task)

    async def push_unsent_outcome(self, task_id: str) -> None:
        """Push the outcome the task's manager wrote, if no event delivered it.

        Called once the execution is over. The consumer pushes every outcome
        it writes itself; what is left is an outcome written past it - the
        ``FAILED`` a2a-sdk's producer stores when the agent raises before it
        queued a final status. The stored Task is sent, as for any outcome.
        An outcome another writer stored (a cancel from another server, the
        abandoned-task settlement) is not this manager's write, and its
        writer has pushed it.

        Args:
            task_id: Identifier of the task.
        """
        outcome = self._task_manager.written_outcome
        if outcome is None or outcome == self._pushed_outcome:
            return
        task = await self._task_manager.get_task()
        if task is None or task.status.state != outcome:
            return
        logger.debug(
            "Push for task %s: sending the %s no event delivered",
            task_id,
            TaskState.Name(outcome),
        )
        await self.send_notification(task_id, task)

    @staticmethod
    def _is_non_active_status(event: PushNotificationEvent) -> bool:
        return (
                isinstance(event, TaskStatusUpdateEvent)
                and event.status.state in NON_ACTIVE_TASK_STATES
        )


__all__ = ['TerminalTaskPushSender']

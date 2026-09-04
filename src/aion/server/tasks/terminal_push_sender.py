"""Push-notification decorator that replaces non-active status updates with the full Task."""

import logging
from a2a.server.tasks.push_notification_sender import PushNotificationEvent, PushNotificationSender
from a2a.types import Task, TaskStatusUpdateEvent

from aion.server.a2a.constants import NON_ACTIVE_TASK_STATES
from aion.server.tasks.protocols import AionTaskManagerProtocol

logger = logging.getLogger(__name__)


class TerminalTaskPushSender(PushNotificationSender):
    """Mirrors TerminalTaskProjection on the push channel.

    The event consumer dispatches raw events to webhooks (see
    ``EventConsumer._update_task_state`` in a2a-sdk), so without this wrapper
    the last notification a client receives is the bare non-active
    ``TaskStatusUpdateEvent``. The wrapper substitutes the Task snapshot,
    keeping the outbound contract: a task leaves the active states through
    exactly one event — the full Task.

    The snapshot is read through the task's own manager rather than the task
    store: push dispatch runs in the background consumer, which has no request
    context, and the manager carries the call context the store is scoped by.
    That makes the wrapper per-task — it is bound by the registry that creates
    the ActiveTask, not installed globally on the handler.
    """

    def __init__(self, inner: PushNotificationSender, task_manager: AionTaskManagerProtocol) -> None:
        self._inner = inner
        self._task_manager = task_manager

    async def send_notification(self, task_id: str, event: PushNotificationEvent) -> None:
        if not self._is_non_active_status(event):
            await self._inner.send_notification(task_id, event)
            return

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

    @staticmethod
    def _is_non_active_status(event: PushNotificationEvent) -> bool:
        return (
                isinstance(event, TaskStatusUpdateEvent)
                and event.status.state in NON_ACTIVE_TASK_STATES
        )


__all__ = ['TerminalTaskPushSender']

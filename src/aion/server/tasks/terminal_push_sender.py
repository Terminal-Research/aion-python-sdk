"""Per-task push-notification decorator: every event the task stores, outcomes as the full Task."""

import logging
from a2a.server.tasks.push_notification_sender import PushNotificationEvent, PushNotificationSender
from a2a.types import Task, TaskState, TaskStatusUpdateEvent

from aion.server.a2a.constants import NON_ACTIVE_TASK_STATES
from aion.server.files.links import link_scope_of
from aion.server.files.storage import UploadContext
from aion.server.tasks.protocols import AionTaskManagerProtocol
from aion.server.tasks.push_sender import is_task_outcome, send_with_link_scope

logger = logging.getLogger(__name__)


def current_link_scope(task_manager: object) -> UploadContext | None:
    """The scope the request ``task_manager`` acts for asks for file links in; None asks as the daemon.

    a2a-sdk's ``ActiveTask`` points its task manager at the call context of
    the request it acts for: ``start`` at the request that created the
    ``ActiveTask``, the producer at each request as its turn begins, and
    ``cancel`` at the canceller's, at once. The producer moves on only after
    the consumer has handled every event of the turn before - it waits for
    the request lock the consumer releases at ``_RequestCompleted`` - so an
    event the consumer pushes is queued while the manager acts for the
    request whose turn produced it, or for the request that cancelled that
    turn. The scope is the one that request's handler kept
    (``link_scope_of``).
    """
    return link_scope_of(getattr(task_manager, "_call_context", None))


class TerminalTaskPushSender(PushNotificationSender):
    """Shapes the push channel of one task: what the task stores.

    The event consumer dispatches every event it handles to the webhooks (see
    ``EventConsumer._update_task_state`` in a2a-sdk), and the task manager
    stores every one of them, so a receiver gets the events as they are,
    with one exception: a non-active status update is replaced by the Task
    snapshot, mirroring ``TerminalTaskProjection``. A task leaves the active
    states through exactly one event - the full Task.

    An outcome can also be written without an event the consumer pushes: when
    the agent raises, a2a-sdk's producer writes ``FAILED`` into the store
    directly. ``push_unsent_outcome`` sends that one once the execution is
    over, and an outcome a cancelled consumer stopped before it was queued
    as well. Once queued, an event is the sender's to deliver: its queue
    retries it, and logs it when it gives up (``AionPushNotificationSender``).

    The snapshot is read through the task's own manager rather than the task
    store: push dispatch runs in the background consumer, which has no request
    context, and the manager carries the call context the store is scoped by.
    That makes the wrapper per-task — it is bound by the registry that creates
    the ActiveTask, not installed globally on the handler.

    Each delivery asks for the download links of stored files in the scope
    of the request the manager acts for when the event is queued
    (``current_link_scope``), kept with it while it waits: the usage
    attribution of the message whose turn produced the event, on whose
    behalf the turn stores and reads its files. A message queued into a running task runs its turn on the same
    ``ActiveTask``, and its pushes ask on its own behalf. After a cancel on
    this server, what is left of the cancelled turn - its ``CANCELED``
    among it - asks on the canceller's behalf. A webhook is not bound to
    an attribution: the push configs of a task receive every push of it.
    """

    def __init__(
        self,
        inner: PushNotificationSender,
        task_manager: AionTaskManagerProtocol,
    ) -> None:
        self._inner = inner
        self._task_manager = task_manager
        # The non-active state of the last outcome this wrapper handed to the
        # sender, compared with the manager's ``written_outcome``.
        self._queued_outcome: TaskState | None = None

    async def send_notification(self, task_id: str, event: PushNotificationEvent) -> None:
        link_scope = current_link_scope(self._task_manager)
        if self._is_non_active_status(event):
            event = await self._outcome_snapshot(task_id, event)

        taken = await send_with_link_scope(self._inner, task_id, event, link_scope)
        # Recorded once the sender has taken the outcome. One it did not take -
        # its config store unreadable, or its consumer cancelled before the
        # outcome was queued - is sent again by ``push_unsent_outcome``.
        if taken and is_task_outcome(event):
            self._queued_outcome = event.status.state

    async def _outcome_snapshot(
        self, task_id: str, event: TaskStatusUpdateEvent
    ) -> Task | TaskStatusUpdateEvent:
        """The Task to push in place of a non-active status update."""
        task = await self._task_manager.get_task()
        if task is None:
            # A non-active transition is persisted before dispatch, so a miss
            # means the store is inconsistent. Deliver the raw event rather
            # than leave the client without an outcome.
            logger.error("Push for task %s: final Task missing from the store", task_id)
            return event

        if task.status.state not in NON_ACTIVE_TASK_STATES:
            # Defensive: the snapshot lags the event. Settle a copy with the
            # status the event announced, mirroring TerminalTaskProjection.
            settled = Task()
            settled.CopyFrom(task)
            settled.status.CopyFrom(event.status)
            task = settled

        return task

    async def push_unsent_outcome(self, task_id: str) -> None:
        """Push the outcome the task's manager wrote, if no event delivered it.

        Called once the execution is over. The consumer pushes every outcome
        it writes itself; what is left is an outcome written past it - the
        ``FAILED`` a2a-sdk's producer stores when the agent raises before it
        queued a final status - or one the sender did not take: its consumer
        cancelled before the outcome was queued, or the push configs
        unreadable. The stored Task is sent, as for any outcome,
        in the scope of the request the manager acts for: the one whose turn
        wrote the outcome, or the canceller of that turn. An outcome another
        writer stored (a cancel from another server, the abandoned-task
        settlement) is not this manager's write, and its writer has pushed
        it.

        Args:
            task_id: Identifier of the task.
        """
        outcome = self._task_manager.written_outcome
        if outcome is None or outcome == self._queued_outcome:
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


__all__ = ['TerminalTaskPushSender', 'current_link_scope']

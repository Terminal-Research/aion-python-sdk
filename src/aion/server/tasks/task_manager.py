"""Aion-specific task manager that orchestrates event routing and persistence."""

import asyncio
import logging
from collections.abc import Callable

from a2a.server.cluster.task_store import (
    ConcurrentTaskModificationError,
    LegacyTaskStoreAdapter,
    VersionedTaskStore,
)
from a2a.server.events import Event
from a2a.server.tasks import TaskManager
from a2a.types import Message, Task, TaskState, TaskStatus, TaskStatusUpdateEvent
from aion.server.a2a.constants import (
    INTERRUPT_TASK_STATES,
    NON_ACTIVE_TASK_STATES,
    TERMINAL_TASK_STATES,
)
from aion.server.a2a.utils import (
    is_message_recorded,
    is_task_interrupted,
    join_streamed_answer,
    record_closing_message,
)
from aion.server.agent.execution.scope import set_task_status
from typing import override


logger = logging.getLogger(__name__)


class AionTaskManager(TaskManager):
    """
    Extended task manager.

    Inherits from the base TaskManager and adds capabilities for automatically finding and assigning
    the last task from a given context, with optional filtering for interrupted tasks only.

    Message placement follows the A2A convention implemented by the base class:
    `status.message` holds the most recent message and `history` holds everything
    before it. A task that stops - with an outcome, or interrupted to wait for
    input or authorization - also ends its history with the message it stops on
    (`record_closing_message`), so a client reading `history` alone finds the
    answer or the question while `status.message` keeps naming it. The base
    class moves the status message into history when the next status or
    message arrives; a message already recorded there is only cleared from the
    status instead (`_forget_recorded_status_message`, `update_with_message`),
    so it stays in history once.
    """

    def __init__(
        self,
        *args,
        on_interrupted: Callable[[str], None] | None = None,
        **kwargs,
    ):
        """Initialize the manager and bind the registry's interrupt callback.

        The callback is deliberately synchronous and only schedules cleanup.
        ``save_task_event`` runs inside the SDK consumer, which pushes the
        event and hands it to subscribers after the write; the registry closes
        the ActiveTask only once the consumer has done so.
        """
        super().__init__(*args, **kwargs)
        # Every read and write this manager makes carries the request's
        # context, and the store scopes it to that request's owner. A store
        # call with no context is the store's unfiltered access; a manager
        # built without one would turn a user's request into exactly that.
        if self._call_context is None:
            raise ValueError(
                "AionTaskManager needs the request's ServerCallContext; without it "
                "the task store would not be limited to the request's owner"
            )
        self._on_interrupted = on_interrupted
        # The state the store last held, kept as a scalar rather than read off
        # ``_current_task``: that object is mutable and the base class applies
        # the new status to it before it asks for the write, so by the time
        # ``_save_task`` runs there is nothing left to compare against.
        # ``None`` means nothing has been read or written yet, so the first
        # write of a non-active state counts as a transition.
        self._persisted_state: TaskState | None = None
        # The non-active state this manager's own last write moved the task
        # into; see ``written_outcome``.
        self._written_outcome: TaskState | None = None
        # Two writers share this manager: the SDK consumer, for the events the
        # agent puts on the queue, and the producer, for the Task and Message
        # events ``AionEventPipeline`` saves itself. Each write names the
        # version the previous one left, so they take turns.
        self._write_lock = asyncio.Lock()

    @property
    def written_outcome(self) -> TaskState | None:
        """The non-active state this manager wrote the task into, or None.

        Set by a write that moves the task into a non-active state, cleared by
        a write of an active one. A state the store brought back - another
        writer's cancel, say - is not this manager's write and does not count.
        The push channel compares it with the outcome it delivered, to find an
        outcome that was written without an event to carry it: the ``FAILED``
        a2a-sdk's producer writes directly when the agent raises.
        """
        return self._written_outcome

    @override
    def invalidate(self) -> None:
        """Drop the cached snapshot, and with it the state it was read in.

        a2a-sdk calls this before each request and after a write another
        writer overtook, so the next read is the store's. That read is the new
        baseline: what the store holds now, not what this manager last saw.
        """
        super().invalidate()
        self._persisted_state = None

    @override
    async def get_task(self) -> Task | None:
        """Return the task, recording the state a store read brought back."""
        task = await super().get_task()
        self._remember_loaded_state(task)
        return task

    @override
    async def ensure_task_id(self, task_id: str, context_id: str) -> Task:
        """Return the task for an event, recording a state read from the store.

        A task the base class creates here is persisted by that call, so its
        state is recorded by the write instead.
        """
        task = await super().ensure_task_id(task_id, context_id)
        self._remember_loaded_state(task)
        return task

    def _remember_loaded_state(self, task: Task | None) -> None:
        """Adopt a loaded task's state as the baseline, once.

        Only the first observation after a store read counts: later reads see
        the manager's own cache, which already carries whatever this turn has
        written.
        """
        if task is not None and self._persisted_state is None:
            self._persisted_state = task.status.state

    @override
    async def _save_task(self, task: Task) -> None:
        """Persist through the store, then act on a move into an interrupt.

        Writes of this manager take turns (``_write_lock``): the SDK consumer
        and ``AionEventPipeline._save_silently`` both write through it, and a
        write started from a version the other has just moved on would be
        refused as stale.

        Initial Task snapshots bypass ``process`` in the event consumer, so
        their missing timestamp is assigned here before storage and delivery.

        Every write passes through here, whichever path built the task, so
        this is also where a task that stops - with an outcome or on an
        interrupt - gets the message it stops on added to its history
        (``record_closing_message``) and its streamed answer joined into one
        part (``join_streamed_answer``).

        A task the store holds as terminal is final. The version check alone
        does not ensure that: the SDK consumer rereads the task when another
        writer overtakes it - another server cancelling it, say - and a Task
        or Message the producer saves directly would then be written over the
        fresh version. So once the store holds a terminal state:

        * a write in another state is refused as the overtaken write it is,
          with ``ConcurrentTaskModificationError``;
        * a write in the same state - a late reply, a late artifact, the
          consumer recording the ``FAILED`` a failing producer already wrote -
          is not written at all. The cached snapshot the write was applied to
          is dropped, so nothing reads the unwritten change back from it.

        The interrupt effect follows the transition, not the state being
        written: the SDK consumer saves the task once more in its interrupt
        state when it records the incoming user message of a resumed turn,
        before the turn's first event is applied. Treating that write as a
        fresh interrupt would tear down the execution that just started.

        Raises:
            ConcurrentTaskModificationError: If the store holds the task in
                another terminal state, or the write is stale.
        """
        async with self._write_lock:
            previous_state = self._persisted_state
            if previous_state in TERMINAL_TASK_STATES:
                if task.status.state != previous_state:
                    raise ConcurrentTaskModificationError(task.id)
                logger.debug(
                    "Task %s is already %s; not writing to it again",
                    task.id,
                    TaskState.Name(previous_state),
                )
                self.invalidate()
                return
            if not task.status.HasField("timestamp"):
                task.status.timestamp.GetCurrentTime()
            record_closing_message(task)
            join_streamed_answer(task)
            await super()._save_task(task)

            state = task.status.state
            self._persisted_state = state
            if state not in NON_ACTIVE_TASK_STATES:
                self._written_outcome = None
            elif state != previous_state:
                self._written_outcome = state
        if state not in NON_ACTIVE_TASK_STATES or state == previous_state:
            return

        if state in INTERRUPT_TASK_STATES and self._on_interrupted is not None:
            self._on_interrupted(task.id)

    @override
    async def process(self, event: Event) -> Event:
        """Processes an event, updates the task state if applicable, stores it, and returns the event.

        If the event is task-related (`Task`, `TaskStatusUpdateEvent`, `TaskArtifactUpdateEvent`),
        the internal task state is updated and persisted.

        Missing status timestamps are assigned before persistence and delivery.
        Streams, push notifications and stored Task snapshots therefore share
        one time instead of each receiver inventing it. Explicit timestamps are
        preserved.

        Args:
            event: The event object received from the agent.

        Returns:
            The same event object that was processed.
        """
        if isinstance(event, Message):
            event = await self._wrap_message_as_status_event(event)

        if isinstance(event, TaskStatusUpdateEvent) and not event.status.HasField("timestamp"):
            event.status.timestamp.GetCurrentTime()

        event = await self._carry_pending_message(event)
        await self._forget_recorded_status_message(event)

        result = await super().process(event)
        self._track_task_status(event)
        return result

    @override
    def update_with_message(self, message: Message, task: Task) -> Task:
        """Add a message to the task's history, after the status message it supersedes.

        The base class moves the current ``status.message`` into history first.
        A status message already recorded there - the question an interrupted
        task stopped on, when the reply arrives - is only cleared from the
        status, so it stays in history once.
        """
        if task.status.HasField('message') and is_message_recorded(task, task.status.message):
            task.status.ClearField('message')
        return super().update_with_message(message, task)

    async def _forget_recorded_status_message(self, event: Event) -> None:
        """Clear a status message already in history before a status update replaces it.

        The base class moves the current ``status.message`` into history when
        a status update arrives. A task that stopped has its message in
        history already (``record_closing_message``), so moving it again would
        list it twice; clearing it from the status leaves it in history once.
        """
        if not isinstance(event, TaskStatusUpdateEvent):
            return
        task = await self.get_task()
        if task is not None and task.status.HasField('message') and is_message_recorded(
            task, task.status.message
        ):
            task.status.ClearField('message')

    async def _carry_pending_message(self, event: Event) -> Event:
        """Keep the current message in status when an update brings none.

        The base class always demotes the pending `status.message` to history
        before applying a new status. That is right when the update carries a
        message of its own - the pending one is no longer the current one -
        and for the bare `WORKING` that announces work, but wrong for two
        updates that carry none:

        * the event that ends the turn: agents usually announce completion
          with a bare status, which would leave the outcome without its answer
          and lose it entirely for a `historyLength=0` request;
        * a `WORKING` update that only changes details in its metadata - the
          object an executable extension keeps in the task metadata, say - which would leave
          the running task with an empty status line.

        So when such an update arrives, the pending message is moved onto it
        and stays the current message. When the update stops the task, the
        write also adds it to the end of history (``_save_task``); a running
        task's message migrates to history when the next message arrives. The
        event delivered to subscribers is
        the original one; only the copy written to the task carries the
        message.
        """
        if not isinstance(event, TaskStatusUpdateEvent) or event.status.HasField('message'):
            return event

        changes_details = (
            event.status.state == TaskState.TASK_STATE_WORKING and bool(event.metadata)
        )
        if event.status.state not in NON_ACTIVE_TASK_STATES and not changes_details:
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

    def _unversioned_store(self):
        """The plain store, for the reads only it offers.

        The registry hands this manager a2a-sdk's versioned store: the one it
        was built with, or ``LegacyTaskStoreAdapter`` around a plain one.
        Context listings belong to the Aion store underneath.
        """
        store = self.task_store
        if isinstance(store, LegacyTaskStoreAdapter):
            return store.store
        if isinstance(store, VersionedTaskStore):
            return store.as_task_store
        return store

    @staticmethod
    def _track_task_status(event: Event) -> None:
        """Update task status in ExecutionScope when a TaskStatusUpdateEvent is received."""
        if not isinstance(event, TaskStatusUpdateEvent):
            return

        state = event.status.state
        set_task_status(state.value if hasattr(state, 'value') else state)

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

        last_task = await self._unversioned_store().get_context_last_task(
            context_id=self.context_id,
            context=self._call_context,
        )
        if last_task is None:
            return None

        if interrupted and not is_task_interrupted(last_task):
            return None

        self.task_id = last_task.id
        # The task's version is not known from this read, so nothing is
        # cached: the next read goes through the store and brings the
        # version back with the task, which the next write compares against.
        self.invalidate()
        self._persisted_state = last_task.status.state
        return last_task

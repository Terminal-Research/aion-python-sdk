"""Per-request pipeline for processing, deduplicating, and routing A2A events."""

import logging
import copy
from a2a.server.events import Event, EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Message, Task, TaskArtifactUpdateEvent, TaskState, TaskStatusUpdateEvent

from aion.core.a2a.enums import ArtifactId
from aion.core.exceptions import AionError
from aion.server.agent.execution.assistant_answer import AssistantAnswer
from aion.server.agent.execution.scope import get_task_manager as exec_scope_get_task_manager
from aion.server.files.a2a import A2AFileTransformer
from aion.server.files.storage import UploadFailure
from aion.server.tasks import A2ATaskDeduplicator
from aion.server.a2a.constants import NON_ACTIVE_TASK_STATES, TERMINAL_TASK_STATES
from aion.server.a2a.response_extensions import ResponseServiceParameters
from aion.server.a2a.utils import (
    is_message_in_task_history,
    is_same_message,
    task_history_message_ids,
)
from typing import Optional

logger = logging.getLogger(__name__)


class OutboundFileNotStoredError(AionError):
    """A file the agent produced could not be stored, so the task fails.

    The client is told only that, through ``public_reason``; each file's own
    reason, with the storage service's answer, is already in the log line the
    transformer wrote for it. ``event`` is the agent's event as it produced
    it, so whoever produced it can forget what it kept of the files.
    """

    def __init__(
            self,
            failures: list[UploadFailure],
            event: Event,
    ) -> None:
        """Initialize the error.

        Args:
            failures: Every failure of the event that carried the files;
                never empty.
            event: The agent's event that carried the files, before any of
                its parts were stored or dropped.
        """
        self.failures = failures
        self.event = event
        codes = ", ".join(sorted({failure.error_code.value for failure in failures}))
        super().__init__(f"{len(failures)} outbound file(s) could not be stored: {codes}")

    @property
    def public_reason(self) -> str:
        """The safe, stable sentence the client reads, from the first failure."""
        return self.failures[0].public_reason


def _event_debug_info(event) -> str:
    """Return a compact human-readable description of an A2A event for debug logging."""
    if isinstance(event, TaskStatusUpdateEvent):
        state = TaskState.Name(event.status.state)
        msg_id = event.status.message.message_id if event.status.HasField("message") else None
        return f"TaskStatusUpdateEvent(state={state}, message_id={msg_id})"
    if isinstance(event, Task):
        return f"Task(id={event.id})"
    if isinstance(event, Message):
        return f"Message(id={event.message_id})"
    return type(event).__name__


class AionEventPipeline:
    """Orchestrates per-event processing: starting the task, transforms, routing.

    A new task opens as ``SUBMITTED`` and moves to ``WORKING`` with the first
    event of its turn that reaches the client or the task. A status update
    states the task's state itself, so it carries that move. Anything else -
    an artifact, a message or task snapshot the pipeline stores without
    streaming it - is preceded by a ``WORKING`` status: the task is never
    ``SUBMITTED`` while its output reaches the client, and a turn that opens
    with a status of its own gets no status without a message or details in
    front of it.

    Two things of the SDK's own artifacts happen here, the same for every
    executor:

    - every thought is added to the task's thoughts: the first one of the
      task creates the artifact, the rest extend it;
    - a task that completes without having produced anything but messages
      gets the last one as its answer (``AssistantAnswer``).
    """

    def __init__(
            self,
            event_queue: EventQueue,
            task_updater: TaskUpdater,
            file_transformer: Optional[A2AFileTransformer] = None,
            task_started: bool = False,
            response_parameters: ResponseServiceParameters = ResponseServiceParameters(),
            prior_message_ids: frozenset[str] = frozenset(),
    ):
        """
        Args:
            event_queue: Queue the processed events are emitted to.
            task_updater: Updater used to move the task to ``WORKING`` when
                the first event does not.
            file_transformer: Optional transformer for inline file parts.
            task_started: True when the task is already working, as the
                executor makes a resumed task before its turn runs.
            response_parameters: Verified activation for this invocation only.
            prior_message_ids: Existing history/status messages to preserve.
        """
        self._queue = event_queue
        self._task_updater = task_updater
        self._file_transformer = file_transformer
        self._task_started = task_started
        self._terminal_seen = False
        self._response_parameters = response_parameters
        self._prior_message_ids = prior_message_ids
        self._deduplicator: Optional[A2ATaskDeduplicator] = None
        self._answer = AssistantAnswer()
        self._task_has_thoughts: Optional[bool] = None

    @property
    def _task_manager(self):
        return exec_scope_get_task_manager()

    @property
    def terminal_seen(self) -> bool:
        """Whether a terminal task state has already passed through the pipeline.

        The caller uses this to decide whether a crashing producer still owes
        the task a terminal status: a producer that failed *after* reporting
        its own outcome has already closed the task, and a second terminal
        event would contradict the first.
        """
        return self._terminal_seen

    async def process(self, event) -> None:
        """Prepare one agent event and deliver what is left of it.

        Raises:
            OutboundFileNotStoredError: The event carried a file that could
                not be stored. The rest of the event has been delivered by
                then, so an answer sent alongside the file reaches the client
                before the task fails.
        """
        produced = event
        event, failures = await self._prepare_event(event)
        if event is not None:
            if failures and self._declares_outcome(event):
                # The FAILED that follows is the task's outcome, not the state
                # this event declared with the file still in it.
                event.status.state = TaskState.TASK_STATE_WORKING
            await self._deliver(event)
        if failures:
            raise OutboundFileNotStoredError(failures, produced)

    async def _deliver(self, event) -> None:
        event = await self._deduplicate_event(event)
        if event is None:
            return

        await self._start_task(event)

        if self._is_thought(event):
            await self._add_thought(event)
        elif self._completes(event):
            answer = self._answer.for_completion(event, await self._stored_task())
            if answer is not None and isinstance(event, Task):
                # A whole task is stored as it is: the answer goes in it.
                event.artifacts.append(answer.artifact)
            elif answer is not None:
                await self._emit_to_client(answer)
        self._answer.observe(event)

        # Task and Message events are persisted silently (not streamed to client)
        if isinstance(event, (Task, Message)):
            if isinstance(event, Task):
                await self._flush_pending_status_message(event)
            await self._save_silently(event)
        else:
            # All other events are streamed to client
            await self._emit_to_client(event)

        self._note_terminal_state(event)

        if self._deduplicator is not None:
            self._deduplicator.apply_processed_item(event)

    @staticmethod
    def _is_thought(event) -> bool:
        return (
            isinstance(event, TaskArtifactUpdateEvent)
            and event.artifact.artifact_id == ArtifactId.THINKING_DELTA.value
        )

    @staticmethod
    def _completes(event) -> bool:
        return (
            isinstance(event, (Task, TaskStatusUpdateEvent))
            and event.status.state == TaskState.TASK_STATE_COMPLETED
        )

    async def _add_thought(self, event: TaskArtifactUpdateEvent) -> None:
        """Make the thought extend the task's thoughts, or start them when it is the task's first."""
        if self._task_has_thoughts is None:
            stored = await self._stored_task()
            self._task_has_thoughts = stored is not None and any(
                artifact.artifact_id == ArtifactId.THINKING_DELTA.value for artifact in stored.artifacts
            )
        event.append = self._task_has_thoughts
        event.last_chunk = False
        self._task_has_thoughts = True

    async def _stored_task(self) -> Optional[Task]:
        try:
            task_manager = self._task_manager
        except RuntimeError:
            return None
        return await task_manager.get_task() if task_manager is not None else None

    @staticmethod
    def _declares_outcome(event) -> bool:
        """Whether the event ends the turn: a status update or a task in a non-active state."""
        return (
            isinstance(event, (Task, TaskStatusUpdateEvent))
            and event.status.state in NON_ACTIVE_TASK_STATES
        )

    def _note_terminal_state(self, event) -> None:
        """Record that this event closed the task, whatever shape it arrived in."""
        if isinstance(event, (Task, TaskStatusUpdateEvent)):
            self._terminal_seen = (
                self._terminal_seen or event.status.state in TERMINAL_TASK_STATES
            )

    async def _flush_pending_status_message(self, incoming_task: Task) -> None:
        """Move pending status.message from current task into incoming task's history.

        When a Task object is saved directly (bypassing the status-update chain),
        any message accumulated in the current task's status.message would be lost.
        Inserts the pending message after messages already known to the current task
        and before new messages introduced by the incoming task. A pending message
        the incoming task still carries as its own status message is not lost, and
        stays in the status.
        """
        task_manager = self._task_manager
        if task_manager is None:
            return

        current_task = await task_manager.get_task()
        if current_task is None or not current_task.status.HasField('message'):
            return

        pending = current_task.status.message
        if is_message_in_task_history(incoming_task, message=pending):
            return
        if incoming_task.status.HasField('message') and is_same_message(incoming_task.status.message, pending):
            return

        # Insert after messages already in current task's history, before new ones
        current_history_ids = task_history_message_ids(current_task)
        insert_idx = 0
        for i, msg in enumerate(incoming_task.history):
            if msg.message_id and msg.message_id in current_history_ids:
                insert_idx = i + 1
        incoming_task.history.insert(insert_idx, pending)

    async def _save_silently(self, event: Task | Message) -> None:
        """Persist event to database without emitting to client.

        Task and Message events bypass the event queue and are saved
        directly to the database without being streamed to the client.
        """
        task_manager = self._task_manager
        if task_manager:
            await task_manager.process(event)
        else:
            logger.warning("Cannot process event silently: task_manager is not initialized.")

    async def _emit_to_client(self, event) -> None:
        """Put the event on the queue, for the consumer to deliver and persist.

        Unlike a Task or a Message, which this pipeline stores itself.
        """
        event_copy = copy.deepcopy(event)
        await self._queue.enqueue_event(event_copy)

    async def _start_task(self, event) -> None:
        """Move the task to ``WORKING`` ahead of the first event that does not move it itself."""
        if self._task_started:
            return
        self._task_started = True
        if self._starts_the_task(event):
            return
        await self._task_updater.update_status(TaskState.TASK_STATE_WORKING)

    @staticmethod
    def _starts_the_task(event) -> bool:
        """Whether delivering the event moves a ``SUBMITTED`` task out of that state.

        Only a status update does. A ``Message`` or ``Task`` is stored
        silently, under the state the task already has, and the ``WORKING``
        written before it is also what moves a message left in
        ``status.message`` into the history when the event consumer applies
        it.
        """
        return isinstance(event, TaskStatusUpdateEvent)

    async def _prepare_event(self, event) -> tuple[object, list[UploadFailure]]:
        """Return the event ready for delivery and the files it could not keep.

        The event is ``None`` when nothing of it is left to deliver.
        """
        event = self._response_parameters.annotate(event, self._prior_message_ids)
        if not self._file_transformer:
            return event, []
        transform = await self._file_transformer.transform(event)
        return transform.event, transform.report.failures

    async def _deduplicate_event(self, event):
        """Drop an event the task already holds, and normalize the rest against the task.

        The deduplicator starts from the task as stored when the turn's first
        event reaches it, and then learns every event it lets through. A new
        task may not be stored yet at that point: the SDK consumer stores the
        task the executor announced on the far side of the queue, and the
        agent can be quicker. The deduplicator then starts from the task's
        identity alone - nothing the turn produces is in the task yet - so a
        message the agent emits twice, as a reply and again from its
        framework's state, still reaches the client once. A whole ``Task``
        the agent sends by then is the task as the agent has it: it goes on
        as sent, and the deduplicator starts from it.
        """
        # Skip deduplication for artifact updates - each update should be streamed
        # as a new part of the artifact (e.g., new chunks during streaming)
        if isinstance(event, TaskArtifactUpdateEvent):
            return event

        try:
            task_manager = self._task_manager
        except RuntimeError:
            return event

        if task_manager is None:
            return event

        if self._deduplicator is None:
            original_task = await task_manager.get_task()
            goes_as_sent = original_task is None and isinstance(event, Task)
            if goes_as_sent:
                original_task = event
            elif original_task is None:
                original_task = Task(id=self._task_updater.task_id, context_id=self._task_updater.context_id)
            # Events here are produced by the server's executors,
            # not the public API, so they are allowed to carry platform-owned
            # (`https://docs.aion.to`) metadata — progress structs and the
            # plan-gate stash reach Task.metadata through this path.
            self._deduplicator = A2ATaskDeduplicator(original_task, trusted_source=True)
            if goes_as_sent:
                return event

        try:
            result = self._deduplicator.deduplicate(event)
            if result is None:
                logger.debug("Deduplicator dropped event: %s", _event_debug_info(event))
            return result
        except TypeError:
            return event

"""Outbound stream projection that always closes a stream with a full Task."""

import logging
from a2a.server.context import ServerCallContext
from a2a.server.events import Event
from a2a.types import Task, TaskState, TaskStatusUpdateEvent
from a2a.utils.errors import InternalError
from aion.server.a2a.constants import NON_ACTIVE_TASK_STATES
from collections.abc import AsyncGenerator, AsyncIterable, Callable
from typing import Optional

logger = logging.getLogger(__name__)


class TerminalTaskProjection:
    """Projects the outbound event stream so it always ends with a full Task.

    Inside the server a ``TaskStatusUpdateEvent`` carrying a non-active state
    (terminal or interrupt) is the state-transition primitive: the task manager,
    the deduplicator and push notifications all rely on it. Externally, however,
    the contract is that a task reaches a non-active state through exactly one
    event — the complete ``Task``.

    This projection bridges the two. It withholds non-active status updates and
    emits the stored ``Task`` once the source stream is exhausted, however the
    stream ended:

    * the agent declared an outcome — the SDK consumer persists it before the
      event reaches this projection, so the stored snapshot already carries it;
    * the stream raised — the SDK persists FAILED on both the producer and the
      consumer path before the exception is handed to subscribers, so again the
      stored snapshot carries it. Only a request that never got as far as
      creating a task propagates the error to the transport;
    * the stream ended without an outcome — the stored snapshot is emitted as
      it stands, active state and all.

    That last case is the reason this projection never writes. A subscriber
    cannot tell a finished turn from its own disconnection: ``ActiveTask``
    absorbs the cancellation and ends the subscription normally either way. But
    a disconnected client does not stop the execution, which keeps running and
    records the real outcome itself. Inventing a non-active state here would
    race that writer, and on the resubscribe path it would let an observer
    settle a task that is merely being watched. A turn that ends with the task
    still active is legitimate for the SDK — the task simply stays resumable —
    so the honest close is the snapshot as stored. The one place a state has to
    be supplied is a shutdown, where the execution is known to be dead; that is
    done by the active task registry, which owns the lifecycle.

    A standalone ``Message`` needs no handling here: Aion's executor always
    announces a Task first, so the SDK consumer is in task mode and rejects a
    Message with ``InvalidAgentResponseError`` — which arrives as the failure
    above and still closes the stream with a Task.
    """

    def __init__(
            self,
            task_store,
            call_context: ServerCallContext,
            task_id: Optional[str] = None,
            task_transform: Optional[Callable[[Task], Task]] = None,
    ) -> None:
        """Initialize the projection.

        Args:
            task_store: Store used to read and close the final Task snapshot.
            call_context: Server call context forwarded to the store.
            task_id: Known task id, when the caller already has one (resubscribe).
            task_transform: Optional transform applied to the final Task, used to
                honour request-level options such as ``history_length``.
        """
        self._task_store = task_store
        self._call_context = call_context
        self._task_id = task_id
        self._task_transform = task_transform
        self._withheld: TaskStatusUpdateEvent | None = None
        self._opened = False

    async def project(self, source: AsyncIterable[Event]) -> AsyncGenerator[Event, None]:
        """Yield the source stream, opened and closed by a Task."""
        try:
            async for event in source:
                self._track_task_id(event)

                if not self._opened:
                    self._opened = True
                    opening = await self._opening_snapshot(event)
                    if opening is not None:
                        yield opening
                        if self._restates(opening, event):
                            continue

                if self._is_non_active_status(event):
                    self._withheld = event
                    continue

                # Nothing is expected after a non-active state, but if the agent
                # does emit something, release the withheld event in its
                # original position rather than dropping it.
                if self._withheld is not None:
                    logger.debug("Event after non-active status, releasing withheld update")
                    yield self._withheld
                    self._withheld = None

                yield event
        except Exception as ex:
            failed = await self._close_snapshot(TaskState.TASK_STATE_FAILED)
            if failed is None:
                # The request never produced a task, so there is no terminal
                # state to report — the transport turns this into an error.
                raise
            logger.warning(
                "Stream for task %s raised %s, closing it as FAILED",
                self._task_id,
                type(ex).__name__,
            )
            yield failed
            return

        # A withheld event names the outcome the agent declared; a stream that
        # ended silently declares nothing, and the stored snapshot stands.
        expected_state = self._withheld.status.state if self._withheld is not None else None
        final_task = await self._close_snapshot(expected_state)
        if final_task is not None:
            yield final_task
        elif self._task_id:
            # The consumer persists a transition before handing it to us, so a
            # task that produced events and is then missing means the store is
            # inconsistent. Reporting that beats answering with a snapshot whose
            # id resolves to nothing.
            logger.error("Task %s produced events but is missing from the store", self._task_id)
            raise InternalError(message=f"Final state for task {self._task_id} is unavailable.")

    async def _opening_snapshot(self, first_event: Event) -> Task | None:
        """Return the Task a stream must open with, when the source omits it.

        A freshly created task is announced by the executor as a Task, and
        resubscribe is opened by the SDK the same way. A resumed task is not:
        its stream starts with the working status update. The snapshot is read
        from the store rather than pushed through the event queue on purpose —
        an extra Task in the queue makes the SDK consumer drop the user message
        it is holding (``_handle_initial_task``).

        Args:
            first_event: The first event the source produced.

        Returns:
            The opening Task, or None when the stream already opens correctly
            or no active snapshot is available to open it with.
        """
        if isinstance(first_event, Task) or not self._task_id:
            return None

        task = await self._task_store.get(self._task_id, self._call_context)
        if task is None or task.status.state in NON_ACTIVE_TASK_STATES:
            # A non-active snapshot would read as a terminal event, which is
            # the opposite of what an opening snapshot means.
            return None

        logger.debug("Opening the stream for task %s with a Task snapshot", self._task_id)
        return self._task_transform(task) if self._task_transform else task

    async def _close_snapshot(self, expected_state: Optional[TaskState]) -> Task | None:
        """Load the Task the stream should close with.

        The store is only read. Every outcome a stream can end on is persisted
        by the SDK before it reaches this projection, so the stored snapshot is
        the authoritative answer; writing here would race the execution, which
        outlives a disconnected subscriber.

        ``expected_state`` is what the source stream declared, when it declared
        anything. Finding the store still active despite a declared outcome
        means the SDK's write has not landed, which should not happen — the
        client is answered with the declared state so the stream still closes
        as non-active, but nothing is persisted and the disagreement is logged.

        Args:
            expected_state: Non-active state the source stream declared, or
                None when it ended without declaring one.

        Returns:
            The final Task, or None when the store holds no task.
        """
        if not self._task_id:
            return None

        task = await self._task_store.get(self._task_id, self._call_context)
        if task is None:
            return None

        if expected_state is not None and task.status.state not in NON_ACTIVE_TASK_STATES:
            logger.warning(
                "Stream for task %s declared %s but the store still holds %s",
                task.id,
                TaskState.Name(expected_state),
                TaskState.Name(task.status.state),
            )
            # Answer from a copy: the snapshot that opened this stream may be
            # the very same object, and it has already been sent to the client.
            declared = Task()
            declared.CopyFrom(task)
            declared.status.state = expected_state
            task = declared

        return self._task_transform(task) if self._task_transform else task

    def _track_task_id(self, event: Event) -> None:
        """Remember the task id carried by an outbound event.

        Tracking the id from the stream rather than from the request keeps the
        projection correct for freshly created tasks, whose id is generated
        server-side.
        """
        task_id = event.id if isinstance(event, Task) else getattr(event, 'task_id', None)
        if task_id:
            self._task_id = task_id

    @staticmethod
    def _restates(opening: Task, event: Event) -> bool:
        """Return True if the event only repeats what the opening snapshot said.

        A resumed task is already working by the time its snapshot is read, so
        the working status update that opened the turn carries no state change
        and no message — it would reach the client as a duplicate of the
        snapshot. A new task is unaffected: it opens as SUBMITTED and the same
        event is a real transition.
        """
        return (
                isinstance(event, TaskStatusUpdateEvent)
                and event.status.state == opening.status.state
                and not event.status.HasField('message')
        )

    @staticmethod
    def _is_non_active_status(event: Event) -> bool:
        return (
                isinstance(event, TaskStatusUpdateEvent)
                and event.status.state in NON_ACTIVE_TASK_STATES
        )


__all__ = ['TerminalTaskProjection']

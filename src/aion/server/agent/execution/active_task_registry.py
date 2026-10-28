"""Registry that creates ActiveTask instances wired with AionTaskManager."""

import asyncio
import logging
from typing import Any, override

from a2a.server.agent_execution.active_task import ActiveTask
from a2a.server.agent_execution.active_task_registry import ActiveTaskRegistry
from a2a.server.cluster.task_store import ConcurrentTaskModificationError, StoredTask
from a2a.server.context import ServerCallContext
from a2a.types import Task, TaskState
from a2a.types.a2a_pb2 import Message
from a2a.utils.errors import TaskNotCancelableError, TaskNotFoundError

from aion.core.a2a.enums import TaskSettlementReason
from aion.server.a2a.constants import TERMINAL_TASK_STATES
from aion.server.agent.execution.scope import set_task_manager
from aion.server.shutdown import shutdown_budget
from aion.server.tasks import AionTaskManager, TerminalTaskPushSender, settled_task

logger = logging.getLogger(__name__)


def _require_call_context(call_context: ServerCallContext | None) -> None:
    """Refuse to act for a request whose context was lost on the way.

    The task store treats a call with no context as unfiltered access to every
    owner's tasks. Everything this registry does is on behalf of one A2A
    request, so a missing context is a bug upstream of here, and is not
    allowed to widen into that.
    """
    if call_context is None:
        raise ValueError(
            "the active task registry acts for one request and needs its "
            "ServerCallContext"
        )


async def _wait_until_finished(active_task: ActiveTask) -> None:
    """Wait until ``ActiveTask.is_finished`` turns true.

    The SDK reports the state but offers nothing to wait on, so this waits on
    the private event behind the property: it is set when the consumer loop
    exits, or as soon as the agent answers with a direct ``Message``, and
    both ``start`` and ``subscribe`` refuse to run afterwards.
    """
    await active_task._is_finished.wait()


async def _wait_until_consumer_is_idle(active_task: ActiveTask) -> None:
    """Wait until the consumer has handled every event queued for it, or has ended.

    The consumer marks an event done (``task_done``) as the last step of
    handling it - after the write, the push and the hand-off to subscribers -
    so ``join`` on its queue returns once nothing it was given is still in
    hand. A consumer that ends first, or an object someone else closes, never
    marks the event it was holding done; ``_is_finished`` covers that case.
    ``_event_queue_agent`` is private to the SDK and the consumer's only
    queue.
    """
    waiters = (
        asyncio.ensure_future(active_task._event_queue_agent.queue.join()),
        asyncio.ensure_future(_wait_until_finished(active_task)),
    )
    try:
        await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for waiter in waiters:
            waiter.cancel()


class AionActiveTaskRegistry(ActiveTaskRegistry):
    """Extends the base registry to inject AionTaskManager and populate the execution scope.

    Everything that decides where a task runs is a2a-sdk's: any instance
    creates an ``ActiveTask`` for any task it receives a request for, and the
    versioned store, when there is one, orders the writes of instances that
    run the same task. This class adds what is Aion's on top of that:

    * the task manager is ``AionTaskManager`` and the push sender is wrapped
      per task in ``TerminalTaskPushSender``, so the push channel carries what
      the task stores; an outcome the execution stored past its consumer is
      pushed once the execution is over;
    * an ``ActiveTask`` is retired once its task reaches ``INPUT_REQUIRED`` or
      ``AUTH_REQUIRED``: it is no longer handed out, and it is closed as soon
      as its consumer has handled the interrupt - pushed it among the rest.
      A finished one is never handed out again either, so the next turn
      starts a fresh ``ActiveTask`` - on whichever instance it arrives at.
      a2a-sdk's ``interrupted_task_timeout``, which releases an interrupted
      ``ActiveTask`` only after it has been idle that long, is therefore not
      passed on to the objects this registry creates;
    * tasks left running by a shutdown are settled as ``FAILED`` and pushed.
      The registry is the only layer that may: an outcome is otherwise always
      written by the execution itself, and the registry is where the
      execution is known to be gone.

    ``_active_tasks`` and ``_task_managers`` are guarded by the base
    registry's ``threading.RLock``, shared with the base methods this class
    inherits. Every critical section is synchronous - no ``await`` between
    entering and leaving - because an ``RLock`` held across an ``await`` lets
    another coroutine on the same thread re-enter it and excludes nothing.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Build the registry with the base registry's arguments."""
        super().__init__(*args, **kwargs)
        # Managers are kept here rather than read off ActiveTask so shutdown
        # can reach the store through the same call context the task ran with:
        # a store resolves the owner from it, and no request context exists at
        # shutdown.
        self._task_managers: dict[str, AionTaskManager] = {}
        self._interruption_tasks: set[asyncio.Task[None]] = set()
        self._interruption_tasks_by_id: dict[str, ActiveTask] = {}

    @override
    async def get(self, task_id: str) -> ActiveTask | None:
        """The live ``ActiveTask`` for ``task_id``, or ``None``.

        A finished object can linger until the SDK's deferred cleanup runs;
        it executes nothing and refuses new subscribers, so it is reported as
        absent. So is an interrupted one waiting to be closed
        (``_is_retiring``): its task waits for input, and the object is about
        to go. The handler then treats the task as running elsewhere, or
        nowhere - which is what it is.
        """
        with self._lock:
            active_task = self._active_tasks.get(task_id)
            if (
                active_task is None
                or active_task.is_finished
                or self._is_retiring(active_task)
            ):
                return None
        return active_task

    @override
    async def get_or_create(
        self,
        task_id: str,
        call_context: ServerCallContext,
        context_id: str | None = None,
        create_task_if_missing: bool = False,
        initial_message: Message | None = None,
    ) -> ActiveTask:
        """Retrieves an existing ActiveTask or creates a new one.

        Mirrors the base implementation so that ``AionTaskManager`` can be
        substituted for the SDK's ``TaskManager`` and the outbound push
        projection can be bound per task. Because the body is a
        reimplementation rather than a ``super()`` call, it must track the
        base signature and preconditions exactly.

        A registered object that has finished is closed and replaced rather
        than returned: ``start`` and ``subscribe`` refuse to run on it. An
        interrupted one waiting to be closed is waited for first, then
        replaced the same way: its consumer is still handling the interrupt,
        and the next turn must not run on an object about to be closed.

        Args:
            task_id: Identifier of the task to retrieve or create.
            call_context: Server call context carried into the task manager.
            context_id: Conversation context the task belongs to, when known.
            create_task_if_missing: Whether ``ActiveTask.start`` should persist
                a task that is absent from the store.
            initial_message: Inbound user message that opened the task. The SDK
                passes this from ``_setup_active_task`` so the task manager can
                record it in history; the consumer later de-duplicates it by
                ``message_id``, so dropping it here would lose the user turn.

        Returns:
            The registered ``ActiveTask`` for ``task_id``.

        Raises:
            RuntimeError: If the registry has already been closed via
                ``aclose()``.
            TaskNotFoundError: If a live task is registered but the caller's
                owner-scoped store does not show it.
        """
        _require_call_context(call_context)
        while True:
            stale: ActiveTask | None = None
            retiring: ActiveTask | None = None
            created: ActiveTask | None = None
            with self._lock:
                if self._closed:
                    raise RuntimeError('ActiveTaskRegistry is closed')

                existing = self._active_tasks.get(task_id)
                if existing is not None and existing.is_finished:
                    stale = existing
                    self._active_tasks.pop(task_id, None)
                    self._task_managers.pop(task_id, None)
                    existing = None
                elif existing is not None and self._is_retiring(existing):
                    retiring = existing
                    existing = None

                if existing is None and stale is None and retiring is None:
                    created = self._create(
                        task_id, call_context, context_id, initial_message
                    )

            if retiring is not None:
                # Its close is already scheduled; once it has finished, the
                # next pass replaces it as stale.
                await _wait_until_finished(retiring)
                continue

            if stale is not None:
                # Closed outside the critical section: aclose drives the SDK
                # cleanup callback, which needs this same lock.
                await stale.aclose()
                continue

            if existing is not None:
                # The cache-hit guard of a2a-sdk's own get_or_create: a live
                # task found by id alone is handed out only to a caller the
                # owner-aware store shows it to. Skipped on the create path,
                # where the send path has already read the task through the
                # store. Outside the lock: the read is I/O.
                if not create_task_if_missing:
                    stored = await self._task_store.get(task_id, call_context)
                    if isinstance(stored, StoredTask):
                        stored = stored.task
                    if not stored:
                        raise TaskNotFoundError
                return existing

            await created.start(
                call_context=call_context,
                create_task_if_missing=create_task_if_missing,
            )
            return created

    def _create(
        self,
        task_id: str,
        call_context: ServerCallContext,
        context_id: str | None,
        initial_message: Message | None,
    ) -> ActiveTask:
        """Build and register the ``ActiveTask``; the caller holds the lock."""
        task_manager = AionTaskManager(
            task_id=task_id,
            context_id=context_id,
            task_store=self._task_store,
            initial_message=initial_message,
            context=call_context,
            on_interrupted=self._on_task_interrupted,
        )
        set_task_manager(task_manager)

        # Push dispatch runs in the background consumer with no request
        # context, so the outbound projection is bound per task, to the
        # manager that carries its call context.
        push_sender = self._push_sender
        if push_sender is not None:
            push_sender = TerminalTaskPushSender(
                inner=push_sender,
                task_manager=task_manager,
            )

        active_task = ActiveTask(
            agent_executor=self._agent_executor,
            task_id=task_id,
            task_manager=task_manager,
            push_sender=push_sender,
            on_cleanup=self._on_active_task_cleanup,
            event_stream=self._event_stream,
        )
        self._active_tasks[task_id] = active_task
        self._task_managers[task_id] = task_manager
        return active_task

    @override
    async def _remove_task(self, active_task: ActiveTask) -> None:
        """Drop the object's task manager with it, then push what its execution left unsent.

        The base removes the object only while it is still the registered
        one, so an old object's deferred cleanup never drops a newer one that
        reuses the id after an INPUT/AUTH_REQUIRED turn; the manager is
        dropped on the same terms. The execution is over by then, so this is
        also where an outcome it stored without pushing is pushed
        (``_push_unsent_outcome``).
        """
        await super()._remove_task(active_task)
        with self._lock:
            if self._task_managers.get(active_task.task_id) is active_task._task_manager:
                self._task_managers.pop(active_task.task_id, None)
        await self._push_unsent_outcome(active_task)

    @staticmethod
    async def _push_unsent_outcome(active_task: ActiveTask) -> None:
        """Push the outcome the execution stored past its consumer, if any.

        See ``TerminalTaskPushSender.push_unsent_outcome``. ``_push_sender`` is
        private to the SDK; it is the wrapper ``_create`` bound to this task.
        A failure is logged: the task keeps its outcome either way.
        """
        push_sender = getattr(active_task, "_push_sender", None)
        if not isinstance(push_sender, TerminalTaskPushSender):
            return
        try:
            await push_sender.push_unsent_outcome(active_task.task_id)
        except Exception:
            logger.warning(
                "Could not push the outcome of task %s", active_task.task_id, exc_info=True
            )

    def _is_retiring(self, active_task: ActiveTask) -> bool:
        """Whether this object is interrupted and waiting for its close."""
        return self._interruption_tasks_by_id.get(active_task.task_id) is active_task

    def _on_task_interrupted(self, task_id: str) -> None:
        """Retire the ActiveTask once INPUT/AUTH_REQUIRED is persisted, and schedule its close.

        Called from inside the write, so the close only starts here; see
        ``_close_interrupted_task``. From now on the object is not handed out
        (``get``, ``get_or_create``).
        """
        with self._lock:
            task = self._active_tasks.get(task_id)
            if task is None or self._is_retiring(task):
                return
            pending = asyncio.create_task(
                self._close_interrupted_task(task_id, task),
                name=f"close-interrupted:{task_id}",
            )
            self._interruption_tasks.add(pending)
            self._interruption_tasks_by_id[task_id] = task

        def forget_pending(done: asyncio.Task[None]) -> None:
            """Forget the close task and allow a later incarnation to close."""
            self._interruption_tasks.discard(done)
            with self._lock:
                if self._interruption_tasks_by_id.get(task_id) is task:
                    self._interruption_tasks_by_id.pop(task_id, None)

        pending.add_done_callback(forget_pending)

    async def _close_interrupted_task(self, task_id: str, active_task: ActiveTask) -> None:
        """Close one interrupted ActiveTask once its consumer has handled the interrupt.

        The interrupt is written by the consumer in the middle of handling
        its event: the push to the task's webhooks, the cluster event stream
        and the subscribers come after the write. Closing the object cancels
        the consumer, so the close waits for the consumer to have handled
        everything queued for it; a close at its first await would cut off
        the push of the interrupt, and a caller that learns of the task only
        by push would never hear that it stopped. The task row is not
        settled: the task waits for its next turn.
        """
        try:
            await _wait_until_consumer_is_idle(active_task)
        except Exception:
            logger.error(
                "Could not wait for interrupted task %s to finish its turn", task_id, exc_info=True
            )
        try:
            await active_task.aclose()
        except Exception:
            logger.error("Failed to close interrupted task %s", task_id, exc_info=True)

    async def cancel_local_as_owner(self, task_id: str) -> Task | None:
        """Cancel a task this process is executing right now, as the task's own owner.

        For a caller that is entitled to cancel every task of something larger
        than one owner - ``DeleteContext`` cancelling a shared context's
        tasks. The cancel runs under the context the task's manager was built
        with, so the cancelled task is written into its own owner's partition.
        Returns ``None`` when no live execution is found here, or when its
        owner's context is gone and the caller must cancel through the store.

        Raises:
            TaskNotCancelableError: If this process's own copy of the task
                already has an outcome.
        """
        active_task = await self.get(task_id)
        if active_task is None:
            return None
        call_context = getattr(self._task_managers.get(task_id), "_call_context", None)
        if call_context is None:
            return None
        task = await active_task.cancel(call_context)
        if (
            task.status.state in TERMINAL_TASK_STATES
            and task.status.state != TaskState.TASK_STATE_CANCELED
        ):
            raise TaskNotCancelableError(
                message=(
                    "Task cannot be canceled - current state: "
                    f"{TaskState.Name(task.status.state)}"
                )
            )
        return task

    @override
    async def aclose(self) -> None:
        """Drain the registry, then settle whatever the shutdown interrupted.

        Draining cancels the producer and consumer of every active task. That
        cancellation is a ``BaseException``, so neither the producer's nor the
        consumer's failure handling runs and a task caught mid-turn keeps
        whatever active state it had. Nothing is left to correct it — the
        execution is gone — so the task would be presented as running forever.

        Such a task is settled as ``FAILED``, marked in metadata with
        ``SERVER_SHUTDOWN``. See ``aion.server.tasks.settlement`` for why the
        state is terminal rather than resumable. The settlement is an ordinary
        versioned write: a task another instance has moved on in the meantime
        refuses it, and keeps the state that instance gave it. The settled
        tasks are then pushed, as every stored outcome is: no execution is
        left to push them. Writes and pushes together take at most the
        settlement share of the shutdown budget (``aion.server.shutdown``).
        """
        settlement_seconds = shutdown_budget().settlement
        with self._lock:
            task_managers = list(self._task_managers.values())

        await super().aclose()

        if self._interruption_tasks:
            await asyncio.gather(*self._interruption_tasks, return_exceptions=True)

        deadline = asyncio.get_running_loop().time() + settlement_seconds
        settled: list[Task] = []
        try:
            async with asyncio.timeout_at(deadline):
                for task_manager in task_managers:
                    try:
                        task = await self._settle_interrupted_task(task_manager)
                        if task is not None:
                            settled.append(task)
                    except ConcurrentTaskModificationError:
                        logger.info(
                            "Task %s was moved on by another writer; not settling it after shutdown",
                            task_manager.task_id,
                        )
                    except Exception as exc:
                        logger.error(
                            "Failed to settle task %s after shutdown",
                            task_manager.task_id,
                            exc_info=exc,
                        )
        except TimeoutError:
            logger.warning("Task shutdown settlement exceeded %.1fs", settlement_seconds)

        await self._push_settled(settled, deadline)

        with self._lock:
            self._task_managers.clear()

    async def _push_settled(self, settled: list[Task], deadline: float) -> None:
        """Push the tasks the shutdown settled, all at once, until ``deadline``.

        A receiver that has not answered by the end of the settlement window
        is not waited for: the process is about to stop, and the settled task
        stays readable through ``GetTask``. Each failed or unfinished push is
        logged as a WARNING.
        """
        if not settled or self._push_sender is None:
            return

        async def push(task: Task) -> None:
            try:
                await self._push_sender.send_notification(task.id, task)
            except Exception:
                logger.warning(
                    "Could not push the shutdown settlement of task %s", task.id, exc_info=True
                )

        pushes = [asyncio.create_task(push(task)) for task in settled]
        _, pending = await asyncio.wait(
            pushes, timeout=max(0.0, deadline - asyncio.get_running_loop().time())
        )
        for unfinished in pending:
            unfinished.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
            logger.warning(
                "%d of %d shutdown settlement push(es) did not finish within the "
                "settlement window; the receivers get no notification of them",
                len(pending),
                len(settled),
            )

    @staticmethod
    async def _settle_interrupted_task(task_manager: AionTaskManager) -> Task | None:
        """Mark a single task as interrupted by shutdown, if it is still active.

        Returns:
            The settled task as written, or None when the task needed nothing.
        """
        task = await task_manager.get_task()
        if task is None:
            return None

        settled = settled_task(task, TaskSettlementReason.SERVER_SHUTDOWN)
        if settled is None:
            return None

        logger.info(
            "Settling task %s left in %s by shutdown",
            task.id,
            TaskState.Name(task.status.state),
        )
        await task_manager.save_task_event(settled)
        return settled

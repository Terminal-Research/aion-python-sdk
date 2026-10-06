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
from aion.server.tasks import AionTaskManager, TerminalTaskPushSender, settled_task

logger = logging.getLogger(__name__)

SHUTDOWN_DB_TIMEOUT_SECONDS = 2.0
"""How long shutdown may spend settling the tasks it interrupted."""


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


def _has_finished(active_task: ActiveTask) -> bool:
    """Report whether the SDK has closed this ActiveTask for good.

    ``_is_finished`` is private to the SDK, and it is also the only signal for
    this: the event is set exactly once, when the consumer loop exits, and both
    ``start`` and ``subscribe`` refuse to run afterwards. Read in one place so
    the dependency is a single line to revisit if the SDK grows a public
    equivalent.
    """
    return active_task._is_finished.is_set()


class AionActiveTaskRegistry(ActiveTaskRegistry):
    """Extends the base registry to inject AionTaskManager and populate the execution scope.

    Everything that decides where a task runs is a2a-sdk's: any instance
    creates an ``ActiveTask`` for any task it receives a request for, and the
    versioned store, when there is one, orders the writes of instances that
    run the same task. This class adds what is Aion's on top of that:

    * the task manager is ``AionTaskManager`` and the push sender is wrapped
      per task in ``TerminalTaskPushSender``;
    * an ``ActiveTask`` is closed once its task reaches ``INPUT_REQUIRED`` or
      ``AUTH_REQUIRED``, and a finished one is never handed out again, so the
      next turn starts a fresh ``ActiveTask`` - on whichever instance it
      arrives at;
    * tasks left running by a shutdown are settled as ``FAILED``. The
      registry is the only layer that may: an outcome is otherwise always
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
        absent. The handler then treats the task as running elsewhere, or
        nowhere - which is what it is.
        """
        with self._lock:
            active_task = self._active_tasks.get(task_id)
        if active_task is None or _has_finished(active_task):
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
        than returned: ``start`` and ``subscribe`` refuse to run on it.

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
            created: ActiveTask | None = None
            with self._lock:
                if self._closed:
                    raise RuntimeError('ActiveTaskRegistry is closed')

                existing = self._active_tasks.get(task_id)
                if existing is not None and _has_finished(existing):
                    stale = existing
                    self._active_tasks.pop(task_id, None)
                    self._task_managers.pop(task_id, None)
                    existing = None

                if existing is None and stale is None:
                    created = self._create(
                        task_id, call_context, context_id, initial_message
                    )

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

    def _on_active_task_cleanup(self, active_task: ActiveTask) -> None:
        """Remove an ActiveTask only if it is still the registered incarnation.

        The SDK callback carries the task object, but its base implementation
        schedules cleanup using only ``task_id``. That is racy with the
        INPUT/AUTH replacement path: an old object's deferred cleanup could
        otherwise pop a newer object that already reused the same id.
        """
        cleanup = asyncio.create_task(
            self._remove_task_for_incarnation(active_task),
            name=f"cleanup-active-task:{active_task.task_id}",
        )
        self._cleanup_tasks.add(cleanup)
        cleanup.add_done_callback(self._cleanup_tasks.discard)

    async def _remove_task_for_incarnation(self, active_task: ActiveTask) -> None:
        """Drop one object and its manager, if they are still the registered ones."""
        with self._lock:
            if self._active_tasks.get(active_task.task_id) is not active_task:
                return
            self._active_tasks.pop(active_task.task_id, None)
            if self._task_managers.get(active_task.task_id) is active_task._task_manager:
                self._task_managers.pop(active_task.task_id, None)
            logger.debug("Removed active task for %s", active_task.task_id)

    def _on_task_interrupted(self, task_id: str) -> None:
        """Schedule ActiveTask teardown after INPUT/AUTH_REQUIRED is persisted."""
        task = self._active_tasks.get(task_id)
        if task is None:
            return
        if self._interruption_tasks_by_id.get(task_id) is task:
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
            if self._interruption_tasks_by_id.get(task_id) is task:
                self._interruption_tasks_by_id.pop(task_id, None)

        pending.add_done_callback(forget_pending)

    async def _close_interrupted_task(self, task_id: str, active_task: ActiveTask) -> None:
        """Close one interrupted ActiveTask without settling its task row."""
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
    async def _remove_task(self, task_id: str) -> None:
        """Drop the task manager alongside the base registry's own entry."""
        await super()._remove_task(task_id)
        with self._lock:
            self._task_managers.pop(task_id, None)

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
        refuses it, and keeps the state that instance gave it.
        """
        with self._lock:
            task_managers = list(self._task_managers.values())

        await super().aclose()

        if self._interruption_tasks:
            await asyncio.gather(*self._interruption_tasks, return_exceptions=True)

        try:
            async with asyncio.timeout(SHUTDOWN_DB_TIMEOUT_SECONDS):
                for task_manager in task_managers:
                    try:
                        await self._settle_interrupted_task(task_manager)
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
            logger.warning("Task shutdown settlement exceeded %.1fs", SHUTDOWN_DB_TIMEOUT_SECONDS)

        with self._lock:
            self._task_managers.clear()

    @staticmethod
    async def _settle_interrupted_task(task_manager: AionTaskManager) -> None:
        """Mark a single task as interrupted by shutdown, if it is still active."""
        task = await task_manager.get_task()
        if task is None:
            return

        settled = settled_task(task, TaskSettlementReason.SERVER_SHUTDOWN)
        if settled is None:
            return

        logger.info(
            "Settling task %s left in %s by shutdown",
            task.id,
            TaskState.Name(task.status.state),
        )
        await task_manager.save_task_event(settled)

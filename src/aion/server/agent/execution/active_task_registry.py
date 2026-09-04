"""Registry that creates ActiveTask instances wired with AionTaskManager."""

import asyncio
import logging
from typing import Any, override

from a2a.server.agent_execution.active_task import ActiveTask
from a2a.server.agent_execution.active_task_registry import ActiveTaskRegistry
from a2a.server.context import ServerCallContext
from a2a.types import Task, TaskState
from a2a.types.a2a_pb2 import Message
from a2a.utils.errors import (
    InvalidParamsError,
    TaskNotCancelableError,
    TaskNotFoundError,
)

from aion.core.a2a.enums import TaskSettlementReason
from aion.server.a2a.constants import ACTIVE_TASK_STATES, TERMINAL_TASK_STATES
from aion.server.agent.execution.scope import set_task_manager
from aion.server.tasks import AionTaskManager, TerminalTaskPushSender, settled_task
from aion.server.tasks.ownership import (
    DegenerateOwnershipProvider,
    OwnershipProvider,
    SHUTDOWN_DB_TIMEOUT_SECONDS,
    Busy,
    Claim,
    ControlSignal,
    TaskOwnershipBusy,
)

logger = logging.getLogger(__name__)


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

    The registry also settles tasks left running by a shutdown. It is the only
    layer that may: an outcome is otherwise always written by the execution
    itself, and the registry is where the execution is known to be gone. A
    subscriber cannot make that call — it may have merely disconnected while
    the execution carried on — which is why ``TerminalTaskProjection`` only
    reads.
    """

    def __init__(
        self,
        *args: Any,
        ownership_provider: OwnershipProvider | None = None,
        **kwargs: Any,
    ) -> None:
        """Wire the registry to the ownership provider chosen with the store.

        The provider is passed in rather than read off the task store: the
        pairing of store and provider is one decision, made once in
        ``StoreManager``, and a registry that discovered it by attribute lookup
        would silently fall back to no enforcement whenever the lookup missed.
        """
        super().__init__(*args, **kwargs)
        # Managers are kept here rather than read off ActiveTask so shutdown
        # can reach the store through the same call context the task ran with:
        # a store may resolve ownership from it, and no request context exists
        # at shutdown.
        self._task_managers: dict[str, AionTaskManager] = {}
        self._interruption_tasks: set[asyncio.Task[None]] = set()
        self._interruption_tasks_by_id: dict[str, ActiveTask] = {}
        # Cancellation signaled through the claim rather than requested on
        # this pod directly - see _on_control_signal. Tracked the same way as
        # _interruption_tasks: one background dispatch per ActiveTask
        # incarnation, forgotten once it finishes so a later incarnation of
        # the same task_id can be signaled again.
        self._cancel_signal_tasks: set[asyncio.Task[None]] = set()
        self._cancel_signal_tasks_by_id: dict[str, ActiveTask] = {}
        # Set for the duration of aclose(). While it is set,
        # _remove_task_for_incarnation must not release a claim: aclose() has
        # already taken its own snapshot of the claims held at shutdown, and
        # releases them itself only after _settle_interrupted_task has used
        # them to write the shutdown outcome.
        self._shutting_down = False

        self._ownership: OwnershipProvider = (
            ownership_provider
            if ownership_provider is not None
            else DegenerateOwnershipProvider()
        )
        self._ownership.set_loss_callback(self._on_ownership_lost)
        self._ownership.set_control_signal_callback(self._on_control_signal)
        self._ownership.start()

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
        """
        while True:
            async with self._lock:
                if self._closed:
                    raise RuntimeError('ActiveTaskRegistry is closed')

                existing = self._active_tasks.get(task_id)
                if existing is not None and self._is_reusable(
                    existing, task_id, create_task_if_missing
                ):
                    return existing

                if existing is None:
                    stale_active_task = None
                else:
                    # A finished ActiveTask can linger until the SDK's deferred
                    # cleanup runs, and an object whose lease is gone is no
                    # longer executing anything. Either way the entry is
                    # dropped here so start/subscribe never meets an object
                    # that reports itself already completed.
                    stale_active_task = existing
                    self._active_tasks.pop(task_id, None)
                    self._task_managers.pop(task_id, None)

            if stale_active_task is not None:
                # Closed outside the critical section: aclose drives the SDK
                # cleanup callback, which needs this same lock.
                await stale_active_task.aclose()
                continue

            # Deliberately outside the registry lock. Acquiring a lease is a
            # database round trip that can wait on the task row a cancellation
            # holds, and this lock is shared by every task in the process:
            # holding it across that wait stalls unrelated work.
            claim: Claim | None = None
            if create_task_if_missing:
                acquired = await self._ownership.acquire(task_id)
                if isinstance(acquired, Busy):
                    owner = await self._ownership.current_owner(task_id)
                    raise TaskOwnershipBusy(task_id, owner_instance_id=owner)
                claim = acquired

            try:
                task_manager = AionTaskManager(
                    task_id=task_id,
                    context_id=context_id,
                    task_store=self._task_store,
                    initial_message=initial_message,
                    context=call_context,
                    on_interrupted=self._on_task_interrupted,
                    ownership_provider=self._ownership,
                )

                # An acquire is followed by a fresh read. A task may have
                # become terminal on another instance before this process won
                # the expired claim, and the manager's cache would otherwise
                # write a pre-outcome snapshot back over it.
                if claim is not None:
                    task = await task_manager.refresh_task()
                    if task is not None and task.status.state in TERMINAL_TASK_STATES:
                        raise InvalidParamsError(
                            message=f"Task {task_id} is already in terminal state"
                        )
            except BaseException:
                if claim is not None:
                    await self._ownership.release(claim)
                raise

            closed = False
            raced: ActiveTask | None = None
            active_task: ActiveTask | None = None
            async with self._lock:
                if self._closed:
                    closed = True
                else:
                    raced = self._active_tasks.get(task_id)

                if not closed and raced is None:
                    set_task_manager(task_manager)

                    # Push dispatch runs in the background consumer with no
                    # request context, so the outbound projection is bound per
                    # task, to the manager that carries its call context.
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
                    )
                    self._active_tasks[task_id] = active_task
                    self._task_managers[task_id] = task_manager

            if closed:
                # Released outside the lock for the same reason it was acquired
                # outside it: this is a database round trip.
                if claim is not None:
                    await self._ownership.release(claim)
                raise RuntimeError('ActiveTaskRegistry is closed')

            if raced is not None:
                # Another caller created the object while the lease was being
                # acquired. Its claim and ours are the same map entry, so the
                # lease must not be released here.
                return raced

            try:
                await active_task.start(
                    call_context=call_context,
                    create_task_if_missing=create_task_if_missing,
                )
            except Exception:
                if claim is not None:
                    await self._ownership.release(claim)
                raise
            return active_task

    def _is_reusable(
        self,
        active_task: ActiveTask,
        task_id: str,
        create_task_if_missing: bool,
    ) -> bool:
        """Report whether a registered ActiveTask may serve this request.

        An object that has finished is never reusable. Beyond that, only a
        request that intends to execute needs a lease behind the object: a
        subscriber attaches to whatever the process is already running.
        """
        if _has_finished(active_task):
            return False
        if not create_task_if_missing:
            return True
        if not self._ownership.enforcement_enabled:
            return True
        return self._ownership.claim_for(task_id) is not None

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
        """Drop one object and its manager, then release any lease it still holds.

        A consumer that fails while writing its own failure can miss the
        terminal write that normally releases the claim (see
        ``AionTaskManager._save_task``), leaving the heartbeat renewing a
        lease for work that no longer exists. Releasing it here is a no-op on
        the normal path, where the claim is already gone by the time cleanup
        runs.
        """
        async with self._lock:
            if self._active_tasks.get(active_task.task_id) is not active_task:
                return
            self._active_tasks.pop(active_task.task_id, None)
            if self._task_managers.get(active_task.task_id) is active_task._task_manager:
                self._task_managers.pop(active_task.task_id, None)
            claim = (
                None
                if self._shutting_down
                else self._ownership.claim_for(active_task.task_id)
            )
            logger.debug("Removed active task for %s", active_task.task_id)

        if claim is not None:
            await self._ownership.release(claim)

    async def get_for_attach(
        self,
        task_id: str,
        call_context: ServerCallContext,
    ) -> ActiveTask | None:
        """Return a local running task for a passive subscriber.

        A subscriber never acquires a lease. It may attach to what this process
        is already executing, and otherwise only to a task that nothing is
        executing at all — which the durable state, not the claim table,
        decides: an active state implies a live lease held elsewhere, while a
        terminal or interrupted task legitimately has no owner and is safe to
        replay locally.

        Returns:
            The ``ActiveTask`` to subscribe to, or ``None`` when there is no
            execution to join and none can be started here - a task that
            already has an outcome, or, where ownership is enforced, one that
            is waiting for input. The caller answers those from the store.
            Only single-process serving still attaches to an interrupted task,
            because only there can the same object carry the next turn.

        Raises:
            TaskNotFoundError: If no such task exists.
            TaskOwnershipBusy: If another instance is executing the task.
        """
        stale_active_task = None
        async with self._lock:
            if self._closed:
                raise RuntimeError('ActiveTaskRegistry is closed')
            active_task = self._active_tasks.get(task_id)
            if active_task is not None:
                if self._is_reusable(active_task, task_id, create_task_if_missing=False):
                    return active_task
                stale_active_task = active_task
                self._active_tasks.pop(task_id, None)
                self._task_managers.pop(task_id, None)

        if stale_active_task is not None:
            await stale_active_task.aclose()

        task = await self._task_store.get(task_id, call_context)
        if task is None:
            raise TaskNotFoundError

        if (
            self._ownership.enforcement_enabled
            and task.status.state in ACTIVE_TASK_STATES
        ):
            # Presented as running, and this process is not the one running it.
            # A stream opened here would never produce an event, which a client
            # cannot tell apart from an agent that is thinking.
            owner = await self._ownership.current_owner(task_id)
            raise TaskOwnershipBusy(task_id, owner_instance_id=owner)

        if task.status.state in TERMINAL_TASK_STATES:
            # A settled task is not resumable and never will be, and
            # ``ActiveTask.start`` refuses one: building an execution here
            # would answer the most ordinary reconnect there is - a client
            # reading the result of a finished turn - with "your parameters
            # are wrong". The stored task is the whole answer.
            return None

        if self._ownership.enforcement_enabled:
            # An interrupted task, and this process is not executing it. An
            # object built here would never finish: the SDK ends an execution
            # on a terminal event or on ``aclose``, and a process that only
            # reads the task produces neither, so the registry would hold it
            # until shutdown. Nor could it ever carry the answer - the resume
            # acquires a lease, and an object without one is replaced rather
            # than reused. The stored task is again the whole answer.
            return None

        # Single-process serving, where a resume does reuse this object and a
        # subscriber attached now goes on to see the next turn. Kept for that,
        # and only there: the same object left behind by a process that cannot
        # resume it is a leak.
        return await self.get_or_create(
            task_id,
            call_context=call_context,
            create_task_if_missing=False,
        )

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

    def _on_ownership_lost(self, task_id: str, reason: str) -> None:
        """Fail closed by tearing down the local execution immediately."""
        logger.warning("Task %s lost ownership (%s); stopping execution", task_id, reason)
        self._on_task_interrupted(task_id)

    def _on_control_signal(self, task_id: str, signal: ControlSignal) -> None:
        """React to a request a non-owner left on this process's held claim.

        Called synchronously from the ownership provider's renewal path (see
        ``PostgresOwnershipProvider._notify_control_signal_once``), so this
        only schedules the actual work rather than awaiting it - the same
        discipline ``_on_ownership_lost``/``_on_task_interrupted`` already
        follow, and for the same reason: this runs on the heartbeat's own
        coroutine, which must return to keep renewing every other lease.

        A signal naming a task this process is not currently executing is a
        contradiction - the claim it rode in on is only renewed for a task
        this process holds - and is logged rather than acted on; whatever
        left that mark eventually stops waiting for it (see the reaper's
        overdue-cancel pass, and the request-side wait timeout).
        """
        if signal is not ControlSignal.CANCEL:
            return
        active_task = self._active_tasks.get(task_id)
        if active_task is None:
            logger.warning(
                "Received a cancel signal for %s but this process holds no "
                "active execution for it",
                task_id,
            )
            return
        self._dispatch_cancel(task_id, active_task)

    def _dispatch_cancel(self, task_id: str, active_task: ActiveTask) -> None:
        """Schedule ``active_task.cancel`` at most once per incarnation.

        Mirrors ``_on_task_interrupted``'s scheduling exactly: the identity
        check against ``active_task`` (not just ``task_id``) is what makes a
        second signal for the same incarnation a no-op while still allowing a
        later incarnation - after a resume - to be signaled again.
        """
        if self._cancel_signal_tasks_by_id.get(task_id) is active_task:
            return

        task_manager = self._task_managers.get(task_id)
        call_context = getattr(task_manager, "_call_context", None) or ServerCallContext()

        pending = asyncio.create_task(
            self._run_signaled_cancel(task_id, active_task, call_context),
            name=f"cancel-signal:{task_id}",
        )
        self._cancel_signal_tasks.add(pending)
        self._cancel_signal_tasks_by_id[task_id] = active_task

        def forget_pending(done: asyncio.Task[None]) -> None:
            self._cancel_signal_tasks.discard(done)
            if self._cancel_signal_tasks_by_id.get(task_id) is active_task:
                self._cancel_signal_tasks_by_id.pop(task_id, None)

        pending.add_done_callback(forget_pending)

    async def _run_signaled_cancel(
        self, task_id: str, active_task: ActiveTask, call_context: ServerCallContext
    ) -> None:
        """Run the same graceful cancel a local ``tasks/cancel`` call would run.

        ``ActiveTask.cancel`` is the SDK's own path: it cancels the producer,
        calls the executor's ``cancel`` (rescue included, for evolution runs -
        see ``EvolutionHandler.cancel``), and waits for the consumer to persist
        the terminal outcome. Using it here rather than a bespoke teardown is
        what lets a cross-pod cancellation get exactly the same result a
        same-pod one does.

        Delivery of the signal that led here is one-shot per claim
        incarnation (see ``PostgresOwnershipProvider._notify_control_signal_once``),
        so a failure below would otherwise go unretried until the reaper's
        much slower ``CANCEL_TIMEOUT`` backstop forces the task closed without
        rescue. ``forget_control_signal`` undoes that bookkeeping on any
        failure that leaves the task still cancelable, so the very next
        heartbeat renewal re-delivers the same request and tries again.
        """
        try:
            await active_task.cancel(call_context)
        except TaskNotCancelableError:
            # Reached its own terminal outcome in the race between the signal
            # and the run finishing on its own; nothing left to cancel, and
            # nothing to retry either.
            pass
        except Exception:
            logger.error("Signaled cancel failed for task %s", task_id, exc_info=True)
            self._ownership.forget_control_signal(task_id)

    async def cancel_local(
        self, task_id: str, call_context: ServerCallContext
    ) -> Task | None:
        """Cancel a task this process is executing right now, without a signal round trip.

        The fast path for ``tasks/cancel``: when the request lands on the pod
        already running the task, there is no reason to wait out a heartbeat
        tick the way a cross-pod request must. Returns ``None`` only when no
        live execution is found here, which tells the caller to fall through
        to the claim-signal or direct-store path instead.

        Raises:
            TaskNotCancelableError: If this process's own copy of the task
                already has an outcome. Left to propagate rather than
                swallowed into the ``None`` case: the answer is authoritative
                here, so there is nothing a fallback path would learn that
                this call does not already know.

                ``_has_finished`` alone does not guarantee this: the SDK's own
                ``ActiveTask.cancel`` awaits ``task_manager.get_task()`` before
                it re-checks its finished flag under its own lock, and does
                not raise if the run reaches its own outcome in that window -
                it silently returns the task as it already stood. Re-derived
                here so a race that lands in that window still gets the same
                error branches 2 and 3 already guarantee, instead of a
                same-pod cancel occasionally answering as if it had succeeded.
        """
        active_task = self._active_tasks.get(task_id)
        if active_task is None or _has_finished(active_task):
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
        async with self._lock:
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
        state is terminal rather than resumable.

        The claim each task manager needs to write that outcome is snapshotted
        here, before the drain, and released explicitly afterwards. Draining
        finishes each ``ActiveTask``, which would otherwise release its claim
        through the ordinary cleanup path well before settlement gets to use
        it - see ``_remove_task_for_incarnation``, which is told to stand
        down for the duration via ``_shutting_down``.
        """
        async with self._lock:
            self._shutting_down = True
            task_managers = list(self._task_managers.values())
            claims = [
                claim
                for task_id in self._task_managers
                if (claim := self._ownership.claim_for(task_id)) is not None
            ]

        await super().aclose()

        if self._interruption_tasks:
            await asyncio.gather(*self._interruption_tasks, return_exceptions=True)
        if self._cancel_signal_tasks:
            await asyncio.gather(*self._cancel_signal_tasks, return_exceptions=True)

        try:
            async with asyncio.timeout(SHUTDOWN_DB_TIMEOUT_SECONDS):
                for task_manager in task_managers:
                    try:
                        await self._settle_interrupted_task(task_manager)
                    except Exception as exc:
                        logger.error(
                            "Failed to settle task %s after shutdown",
                            task_manager.task_id,
                            exc_info=exc,
                        )
        except TimeoutError:
            logger.warning("Task shutdown settlement exceeded %.1fs", SHUTDOWN_DB_TIMEOUT_SECONDS)

        async with self._lock:
            self._task_managers.clear()

        for claim in claims:
            await self._ownership.release(claim)

        await self._ownership.stop()

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

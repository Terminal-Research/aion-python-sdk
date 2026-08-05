"""Registry that creates ActiveTask instances wired with AionTaskManager."""

import logging
from typing import Any, override

from a2a.server.agent_execution.active_task import ActiveTask
from a2a.server.agent_execution.active_task_registry import ActiveTaskRegistry
from a2a.server.context import ServerCallContext
from a2a.types import Task, TaskState
from a2a.types.a2a_pb2 import Message

from aion.core.a2a.enums import A2AMetadataKey, TaskSettlementReason
from aion.server.a2a.constants import NON_ACTIVE_TASK_STATES
from aion.server.tasks import AionTaskManager, TerminalTaskPushSender
from aion.server.agent.execution.scope import set_task_manager

logger = logging.getLogger(__name__)


class AionActiveTaskRegistry(ActiveTaskRegistry):
    """Extends the base registry to inject AionTaskManager and populate the execution scope.

    The registry also settles tasks left running by a shutdown. It is the only
    layer that may: an outcome is otherwise always written by the execution
    itself, and the registry is where the execution is known to be gone. A
    subscriber cannot make that call — it may have merely disconnected while
    the execution carried on — which is why ``TerminalTaskProjection`` only
    reads.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Managers are kept here rather than read off ActiveTask so shutdown
        # can reach the store through the same call context the task ran with:
        # a store may resolve ownership from it, and no request context exists
        # at shutdown.
        self._task_managers: dict[str, AionTaskManager] = {}

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
        async with self._lock:
            if self._closed:
                raise RuntimeError('ActiveTaskRegistry is closed')
            if task_id in self._active_tasks:
                return self._active_tasks[task_id]

            task_manager = AionTaskManager(
                task_id=task_id,
                context_id=context_id,
                task_store=self._task_store,
                initial_message=initial_message,
                context=call_context,
            )

            set_task_manager(task_manager)

            # Push dispatch runs in the background consumer with no request
            # context, so the outbound projection is bound per task, to the
            # manager that carries the task's call context.
            push_sender = self._push_sender
            if push_sender is not None:
                push_sender = TerminalTaskPushSender(inner=push_sender, task_manager=task_manager)

            active_task = ActiveTask(
                agent_executor=self._agent_executor,
                task_id=task_id,
                task_manager=task_manager,
                push_sender=push_sender,
                on_cleanup=self._on_active_task_cleanup,
            )
            self._active_tasks[task_id] = active_task
            self._task_managers[task_id] = task_manager

        await active_task.start(
            call_context=call_context,
            create_task_if_missing=create_task_if_missing,
        )
        return active_task

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

        Such a task is settled as ``INPUT_REQUIRED``, marked in metadata with
        ``SERVER_SHUTDOWN``: non-active, so it stops looking alive, but not
        terminal, so a client can still resume it once the server is back.
        """
        async with self._lock:
            task_managers = list(self._task_managers.values())

        await super().aclose()

        for task_manager in task_managers:
            try:
                await self._settle_interrupted_task(task_manager)
            except Exception as exc:
                logger.error(
                    "Failed to settle task %s after shutdown",
                    task_manager.task_id,
                    exc_info=exc,
                )

        async with self._lock:
            self._task_managers.clear()

    @staticmethod
    async def _settle_interrupted_task(task_manager: AionTaskManager) -> None:
        """Mark a single task as interrupted by shutdown, if it is still active."""
        task = await task_manager.get_task()
        if task is None or task.status.state in NON_ACTIVE_TASK_STATES:
            return

        logger.info(
            "Settling task %s left in %s by shutdown",
            task.id,
            TaskState.Name(task.status.state),
        )
        settled = Task()
        settled.CopyFrom(task)
        settled.status.state = TaskState.TASK_STATE_INPUT_REQUIRED
        settled.metadata[A2AMetadataKey.SETTLED_REASON.value] = (
            TaskSettlementReason.SERVER_SHUTDOWN.value
        )
        await task_manager.save_task_event(settled)

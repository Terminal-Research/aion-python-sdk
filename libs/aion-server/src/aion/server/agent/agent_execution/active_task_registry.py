from typing import override

from a2a.server.agent_execution.active_task import ActiveTask
from a2a.server.agent_execution.active_task_registry import ActiveTaskRegistry
from a2a.server.context import ServerCallContext

from aion.server.tasks import AionTaskManager
from aion.shared.agent.execution.scope import AgentExecutionScopeHelper


class AionActiveTaskRegistry(ActiveTaskRegistry):

    @override
    async def get_or_create(
        self,
        task_id: str,
        call_context: ServerCallContext,
        context_id: str | None = None,
        create_task_if_missing: bool = False,
    ) -> ActiveTask:
        """Retrieves an existing ActiveTask or creates a new one."""
        async with self._lock:
            if task_id in self._active_tasks:
                return self._active_tasks[task_id]

            task_manager = AionTaskManager(
                task_id=task_id,
                context_id=context_id,
                task_store=self._task_store,
                initial_message=None,
                context=call_context,
            )

            AgentExecutionScopeHelper.set_task_manager(task_manager)

            active_task = ActiveTask(
                agent_executor=self._agent_executor,
                task_id=task_id,
                task_manager=task_manager,
                push_sender=self._push_sender,
                on_cleanup=self._on_active_task_cleanup,
            )
            self._active_tasks[task_id] = active_task

        await active_task.start(
            call_context=call_context,
            create_task_if_missing=create_task_if_missing,
        )
        return active_task

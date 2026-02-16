"""Framework-agnostic A2A executor for AionAgent."""

from typing import Tuple, Optional

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import (
    InternalError,
    InvalidParamsError,
    Task,
    UnsupportedOperationError,
)
from a2a.utils import new_task
from a2a.utils.errors import ServerError
from a2a.utils.telemetry import trace_function
from aion.shared.agent import AionAgent
from aion.shared.agent.execution import task_context
from aion.shared.logging import get_logger

from aion.server.utils import check_if_task_is_interrupted

logger = get_logger()


class AionAgentRequestExecutor(AgentExecutor):
    """A2A executor adapter for AionAgent.

    Bridges A2A protocol's AgentExecutor interface with AionAgent.
    Plugins are responsible for producing A2A events directly —
    this executor simply enqueues them.
    """

    def __init__(self, aion_agent: AionAgent):
        self.agent = aion_agent
        self._task_updater: TaskUpdater | None = None

    @trace_function
    async def execute(
            self,
            context: RequestContext,
            event_queue: EventQueue,
    ) -> None:
        error = self._validate_request(context)
        if error:
            raise ServerError(error=InvalidParamsError())

        task, is_new_task = await self._get_task_for_execution(context)

        with task_context(str(task.id)):
            self._task_updater = TaskUpdater(event_queue, task.id, task.context_id)

            if is_new_task:
                logger.info("Created task")
                await event_queue.enqueue_event(task)
            else:
                logger.info("Resuming task")

            try:
                event_stream = (
                    self.agent.stream(context=context)
                    if is_new_task
                    else self.agent.resume(context=context)
                )

                first_event = True
                async for agent_event in event_stream:
                    if first_event:
                        await self._update_task_status_working(event_queue, task)
                        first_event = False

                    await event_queue.enqueue_event(agent_event)

            except Exception as ex:
                logger.exception("Execution failed")
                raise ServerError(error=InternalError()) from ex

    async def cancel(
            self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        """Request cancellation of an ongoing task.

        Note: Cancellation support depends on framework capabilities.
        Currently not implemented.

        Args:
            context: A2A request context with task to cancel
            event_queue: Queue for publishing cancellation events

        Raises:
            ServerError: Always raises UnsupportedOperationError
        """
        raise ServerError(error=UnsupportedOperationError())

    @staticmethod
    async def _get_task_for_execution(context: RequestContext) -> Tuple[Task, bool]:
        """Get or create a task for execution.

        Logic:
        1. If current_task exists and is interrupted -> resume it
        2. If current_task exists but not interrupted -> error
        3. If no current_task -> create new task

        Args:
            context: Request context with optional current task

        Returns:
            Tuple of (task, is_new_task)

        Raises:
            ServerError: If task is in terminal state
        """
        current_task = context.current_task

        if current_task is not None:
            if check_if_task_is_interrupted(current_task):
                context.current_task = current_task
                return current_task, False
            else:
                raise ServerError(
                    error=InvalidParamsError(
                        message=f"Task {current_task.id} is in terminal state: "
                                f"{current_task.status.state}"
                    )
                )

        # Create new task
        task = new_task(context.message)
        task.metadata = context.metadata or None
        context.current_task = task
        return task, True

    @staticmethod
    def _validate_request(context: RequestContext) -> bool:
        """Validate the request context.

        Args:
            context: Request context to validate

        Returns:
            True if validation fails, False if valid
        """
        # Add validation logic as needed
        return False

    async def _update_task_status_working(self, event_queue: EventQueue, task: Task) -> None:
        """Update task status to WORKING.

        Args:
            event_queue: Queue to publish status update
            task: Task to update
        """
        if self._task_updater:
            await self._task_updater.start_work()


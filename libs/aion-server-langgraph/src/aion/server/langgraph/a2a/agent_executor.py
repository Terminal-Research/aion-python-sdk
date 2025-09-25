from typing import Tuple, Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import (
    InternalError,
    InvalidParamsError,
    Task,
    UnsupportedOperationError,
)
from a2a.utils import (
    new_task,
)
from a2a.utils.errors import ServerError
from aion.shared.logging.factory import get_logger
from langgraph.types import Command

from aion.server.utils import check_if_task_is_interrupted
from .agent import LanggraphAgent
from .event_producer import LanggraphA2AEventProducer

logger = get_logger(__name__)


class LanggraphAgentExecutor(AgentExecutor):
    """
    LangGraph-based agent executor implementation.

    This executor manages the execution of LangGraph agents, handling task creation,
    resumption, and event streaming. It integrates with the A2A (Agent-to-Agent)
    framework to provide agent execution capabilities with proper error handling
    and event management.
    """

    def __init__(self, graph: Any):
        self.agent = LanggraphAgent(graph)

    async def execute(
            self,
            context: RequestContext,
            event_queue: EventQueue,
    ) -> None:
        """
        Execute the agent with the given context and event queue.

        This method handles the main execution flow including:
        - Request validation
        - Task creation or resumption
        - Event streaming and status updates
        - Error handling and propagation
        """
        error = self._validate_request(context)
        if error:
            raise ServerError(error=InvalidParamsError())

        query = context.get_user_input()
        task, is_new_task = await self._get_task_for_execution(context)
        if is_new_task:
            await event_queue.enqueue_event(task)
        else:
            query = Command(resume=query)

        event_producer = LanggraphA2AEventProducer(event_queue, task)
        firstLoop = True

        try:
            async for item in self.agent.stream(query, task.context_id):
                if firstLoop:
                    await event_producer.update_status_working()
                    firstLoop = False

                await event_producer.handle_event(
                    item['event_type'],
                    item['event'],
                )
        except Exception as e:
            logger.error(f'An error occurred while streaming the response: {e}')
            raise ServerError(error=InternalError()) from e

    async def _get_task_for_execution(self, context: RequestContext) -> Tuple[Task, bool]:
        """
        Retrieve or create a task for execution.

        This method implements the following logic:
        1. If current_task exists and is resumable, return it
        2. If current_task exists but is not resumable, raise an error
        3. If no resumable task found, create a new task

        Returns:
            Tuple[Task, bool]: A tuple containing the task to execute and a boolean
                             indicating whether it's a new task (True) or existing (False).
        """
        current_task = context.current_task
        if current_task is not None:
            if check_if_task_is_interrupted(current_task):
                return current_task, False
            else:
                raise ServerError(error=InvalidParamsError(
                    message=f'Task {current_task.id} is in terminal state: {current_task.status.state}'
                ))

        # just create a new task
        task = new_task(context.message)
        return task, True

    def _validate_request(self, context: RequestContext) -> bool:
        return False

    async def cancel(
            self, request: RequestContext, event_queue: EventQueue
    ) -> Task | None:
        raise ServerError(error=UnsupportedOperationError())

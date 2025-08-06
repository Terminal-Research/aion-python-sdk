import logging
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import (
    InternalError,
    InvalidParamsError,
    Task,
    TaskState,
    UnsupportedOperationError,
)
from a2a.utils import (
    new_task,
)
from a2a.utils.errors import ServerError
from langgraph.types import Command

from .agent import LanggraphAgent
from .event_producer import LanggraphA2AEventProducer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class LanggraphAgentExecutor(AgentExecutor):
    """Currency Conversion ``AgentExecutor`` example."""

    def __init__(self, graph: Any) -> None:
        """Create the executor with the given graph."""
        self.agent = LanggraphAgent(graph)

    async def execute(
            self,
            context: RequestContext,
            event_queue: EventQueue,
    ) -> None:
        error = self._validate_request(context)
        if error:
            raise ServerError(error=InvalidParamsError())

        query = context.get_user_input()
        task = context.current_task
        if task and task.status.state == TaskState.input_required:
            query = Command(resume=query)
        elif not task:
            task = new_task(context.message)
            await event_queue.enqueue_event(task)

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

    def _validate_request(self, context: RequestContext) -> bool:
        return False

    async def cancel(
            self, request: RequestContext, event_queue: EventQueue
    ) -> Task | None:
        raise ServerError(error=UnsupportedOperationError())

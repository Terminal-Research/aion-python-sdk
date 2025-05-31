import logging
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import Event, EventQueue
from a2a.types import (
    InternalError,
    InvalidParamsError,
    Part,
    Task,
    TaskState,
    TextPart,
    UnsupportedOperationError,
)
from a2a.utils import (
    new_agent_text_message,
    new_task,
)
from a2a.utils.errors import ServerError
from .tasks import AionTaskUpdater
from .agent import LanggraphAgent
from .event_producer import LanggraphA2AEventProducer
from langgraph.types import Command

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
            event_queue.enqueue_event(task)
            
        updater = AionTaskUpdater(event_queue, task.id, task.contextId) 
        event_producer = LanggraphA2AEventProducer(event_queue, task)
        firstLoop = True
                
        try:
            async for item in self.agent.stream(query, task.contextId):
                is_task_complete = item['is_task_complete']
                
                if firstLoop:
                    event_producer.update_status_working()
                    firstLoop = False

                if not is_task_complete:
                    event_producer.handle_event(
                        item['event_type'],
                        item['event'],
                        item['is_task_complete'],
                    )
                    # if item['event_type'] == 'interrupt':
                    #     break
                else:
                    updater.add_artifact(
                        [Part(root=TextPart(text=item['content']))],
                        name='conversion_result',
                    )
                    updater.complete()
                    break
        except Exception as e:
            logger.error(f'An error occurred while streaming the response: {e}')
            raise ServerError(error=InternalError()) from e

    def _validate_request(self, context: RequestContext) -> bool:
        return False

    async def cancel(
        self, request: RequestContext, event_queue: EventQueue
    ) -> Task | None:
        raise ServerError(error=UnsupportedOperationError())

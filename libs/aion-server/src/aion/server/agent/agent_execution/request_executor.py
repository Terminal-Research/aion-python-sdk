"""Framework-agnostic A2A executor for AionAgent.

This module provides an adapter that bridges A2A protocol's AgentExecutor
interface with the framework-agnostic AionAgent. It translates A2A concepts
(RequestContext, EventQueue) to AionAgent's unified interface (ExecutionConfig,
ExecutionEvent).
"""

from typing import Tuple

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
from aion.shared.agent.adapters import ExecutionEvent
from aion.shared.agent.execution import task_context
from aion.shared.logging import get_logger

from aion.server.utils import check_if_task_is_interrupted, StreamingArtifactBuilder
from .event_translator import ExecutionEventTranslator
from .event_handler import ExecutionEventHandler

logger = get_logger()


class AionAgentRequestExecutor(AgentExecutor):
    """A2A executor adapter for framework-agnostic AionAgent.

    This class implements the A2A AgentExecutor interface while delegating
    actual execution to AionAgent. It handles:
    - Converting RequestContext to ExecutionConfig
    - Translating ExecutionEvent stream to A2A EventQueue events
    - Task lifecycle management (creation, resumption)
    - Error handling and propagation

    The executor is framework-agnostic: the specific framework logic
    (LangGraph, AutoGen, etc.) is handled by the AionAgent's ExecutorAdapter.

    Architecture:
        1. ExecutorAdapter (e.g., LangGraphExecutor) normalizes framework types
           → ExecutionEvent with simple data (str, dict, list)
        2. ExecutionEventTranslator converts ExecutionEvent to A2A protocol types
           → A2A Message, TaskStatusUpdateEvent, etc.
    """

    def __init__(
            self,
            aion_agent: AionAgent,
            event_translator: ExecutionEventTranslator | None = None,
    ):
        """Initialize executor with an AionAgent.

        Args:
            aion_agent: Framework-agnostic agent instance
            event_translator: Optional custom event translator.
                            If not provided, uses default ExecutionEventTranslator.
        """
        self.agent = aion_agent

        # Use provided translator or default
        if event_translator is None:
            event_translator = ExecutionEventTranslator()

        self.event_translator = event_translator
        self.task_updater: TaskUpdater | None = None
        self.streaming_artifact_builder: StreamingArtifactBuilder | None = None
        self.event_handler: ExecutionEventHandler | None = None

    @trace_function
    async def execute(
            self,
            context: RequestContext,
            event_queue: EventQueue,
    ) -> None:
        """Execute the agent with the given context and event queue.

        Args:
            context: A2A request context with message and task info
            event_queue: Queue for publishing A2A events

        Raises:
            ServerError: If validation fails or execution encounters errors
        """
        # Validate request
        error = self._validate_request(context)
        if error:
            raise ServerError(error=InvalidParamsError())

        # Get or create task
        task, is_new_task = await self._get_task_for_execution(context)

        # Set task_id in context for automatic logging throughout execution
        with task_context(str(task.id)):
            # Create task updater, streaming artifact builder, and event handler
            self.task_updater = TaskUpdater(event_queue, task.id, task.context_id)
            self.streaming_artifact_builder = StreamingArtifactBuilder(task)
            self.event_handler = ExecutionEventHandler(
                task_updater=self.task_updater,
                streaming_artifact_builder=self.streaming_artifact_builder,
                event_translator=self.event_translator,
            )

            if is_new_task:
                logger.info("Created task")
                await event_queue.enqueue_event(task)
            else:
                logger.info("Resuming task")

            try:
                first_event = True

                # Execute agent (stream or resume)
                if is_new_task:
                    # New execution
                    event_stream = self.agent.stream(context=context)
                else:
                    # Resume interrupted execution
                    event_stream = self.agent.resume(context=context)

                # Process events
                async for execution_event in event_stream:
                    # Update task status to working on first event
                    if first_event:
                        await self._update_task_status_working(event_queue, task)
                        first_event = False

                    # Translate and publish event
                    await self._handle_execution_event(execution_event, event_queue, task)

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
        if self.task_updater:
            await self.task_updater.start_work()

    async def _handle_execution_event(
            self,
            execution_event: ExecutionEvent,
            event_queue: EventQueue,
            task: Task,
    ) -> None:
        """Translate ExecutionEvent to A2A events and publish.

        This method delegates to ExecutionEventHandler for actual event processing.

        Args:
            execution_event: Event from agent execution
            event_queue: Queue to publish A2A events
            task: Current task
        """
        if self.event_handler:
            await self.event_handler.handle_event(execution_event, event_queue, task)

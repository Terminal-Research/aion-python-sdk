"""Framework-agnostic A2A executor for AionAgent.

This module provides an adapter that bridges A2A protocol's AgentExecutor
interface with the framework-agnostic AionAgent. It translates A2A concepts
(RequestContext, EventQueue) to AionAgent's unified interface (ExecutionConfig,
ExecutionEvent).
"""

import uuid
from typing import Tuple

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import (
    DataPart,
    InternalError,
    InvalidParamsError,
    Message,
    Part,
    Role,
    Task,
    TextPart,
    UnsupportedOperationError,
)
from a2a.utils import new_task
from a2a.utils.errors import ServerError
from a2a.utils.telemetry import trace_function
from aion.shared.agent import AionAgent
from aion.shared.agent.adapters import ExecutionEvent
from aion.shared.context import set_langgraph_node
from aion.shared.logging import get_logger
from aion.shared.types import MessageType, A2AMetadataKey, ArtifactStreamingStatusReason

from aion.server.utils import check_if_task_is_interrupted, StreamingArtifactBuilder
from .event_translator import ExecutionEventTranslator

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

    @trace_function
    async def execute(
            self,
            context: RequestContext,
            event_queue: EventQueue,
    ) -> None:
        """Execute the agent with the given context and event queue.

        This method orchestrates the full execution flow:
        1. Validate request
        2. Create or resume task
        3. Stream execution through AionAgent
        4. Translate events to A2A format
        5. Handle errors

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

        # Create task updater and streaming artifact builder
        self.task_updater = TaskUpdater(event_queue, task.id, task.context_id)
        self.streaming_artifact_builder = StreamingArtifactBuilder(task)

        if is_new_task:
            logger.info(
                f"Created new task: task_id={task.id}, context_id={task.context_id}, "
                f"agent_id={self.agent.id}, framework={self.agent.framework}"
            )
            await event_queue.enqueue_event(task)
        else:
            logger.info(
                f"Resuming existing task: task_id={task.id}, context_id={task.context_id}, "
                f"agent_id={self.agent.id}, framework={self.agent.framework}"
            )

        # Prepare execution
        user_input = context.get_user_input()
        session_id = task.id
        thread_id = task.context_id

        first_event = True

        try:
            # Execute agent (stream or resume)
            if is_new_task:
                # New execution
                logger.debug(
                    f"Starting new execution: session_id={session_id}, "
                    f"input_length={len(user_input)}"
                )
                event_stream = self.agent.stream(
                    inputs={"input": user_input},
                    session_id=session_id,
                    thread_id=thread_id,
                )
            else:
                # Resume interrupted execution
                logger.debug(
                    f"Resuming execution: session_id={session_id}, "
                    f"input_length={len(user_input)}"
                )
                event_stream = self.agent.resume(
                    session_id=session_id,
                    inputs={"input": user_input} if user_input else None,
                    thread_id=thread_id,
                )

            # Process events
            async for execution_event in event_stream:
                # Update task status to working on first event
                if first_event:
                    await self._update_task_status_working(event_queue, task)
                    first_event = False

                # Translate and publish event
                await self._handle_execution_event(execution_event, event_queue, task)

        except Exception as ex:
            logger.exception(
                f"Execution failed: task_id={task.id}, context_id={task.context_id}, "
                f"agent_id={self.agent.id}, error={ex}"
            )
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
        1. If current_task exists and is interrupted → resume it
        2. If current_task exists but not interrupted → error
        3. If no current_task → create new task

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
            logger.debug(f"Task status updated to WORKING: task_id={task.id}")

    async def _handle_execution_event(
            self,
            execution_event: ExecutionEvent,
            event_queue: EventQueue,
            task: Task,
    ) -> None:
        """Translate ExecutionEvent to A2A events and publish.

        Different event types are handled differently:
        - 'message':
            * is_streaming=True → streaming artifact (AIMessageChunk)
            * is_final=True → finalize artifact + full message (AIMessage)
            * Regular → A2A Message
        - 'custom': Published as DataPart message with EVENT type
        - 'node_update': Updates internal context (NOT sent to client)
        - 'state_update': Logged for debugging (NOT sent to client)
        - 'complete':
            * interrupted → send interrupt message + input_required status
            * not interrupted → completed status
        - 'error': Updates task status to failed

        Args:
            execution_event: Event from agent execution
            event_queue: Queue to publish A2A events
            task: Current task
        """
        event_type = execution_event.event_type
        event_data = execution_event.data
        metadata = execution_event.metadata or {}

        logger.debug(
            f"Processing execution event: type={event_type}, task_id={task.id}"
        )

        # Handle different event types
        if event_type == "message":
            # Handle streaming vs final messages
            is_streaming = metadata.get("is_streaming", False)
            is_final = metadata.get("is_final", False)

            if is_streaming:
                # AIMessageChunk → streaming artifact
                if self.streaming_artifact_builder:
                    artifact_event = self.streaming_artifact_builder.build_streaming_chunk_event(
                        content=event_data,
                        metadata={
                            "status": "active",
                            "status_reason": ArtifactStreamingStatusReason.CHUNK_STREAMING.value,
                        }
                    )
                    await event_queue.enqueue_event(artifact_event)
                    logger.debug(f"Sent streaming artifact: task_id={task.id}")

                # Also update status to working
                if self.task_updater:
                    await self.task_updater.update_status(state="working")

            elif is_final:
                # AIMessage → finalize streaming artifact
                if self.streaming_artifact_builder:
                    finalize_event = self.streaming_artifact_builder.build_finalized_event(
                        metadata={
                            "status": "finalized",
                            "status_reason": ArtifactStreamingStatusReason.COMPLETE_MESSAGE.value,
                        }
                    )
                    if finalize_event:
                        await event_queue.enqueue_event(finalize_event)
                        logger.debug(f"Finalized streaming artifact: task_id={task.id}")

                # Send full message
                a2a_message = self.event_translator.translate_message_event(execution_event, task)
                if a2a_message and self.task_updater:
                    await self.task_updater.update_status(state="working", message=a2a_message)
                    logger.debug(f"Sent final message: task_id={task.id}")

            else:
                # Regular message (not streaming)
                a2a_message = self.event_translator.translate_message_event(execution_event, task)
                if a2a_message:
                    await event_queue.enqueue_event(a2a_message)
                else:
                    logger.warning(f"Failed to translate message event: task_id={task.id}")

        elif event_type == "node_update":
            # Internal event: update active node in context (for LangGraph)
            # Don't send to client - this is debugging/monitoring info
            if isinstance(event_data, dict):
                node_names = list(event_data.keys())
                if node_names:
                    active_node = node_names[0]
                    set_langgraph_node(active_node)
                    logger.debug(
                        f"Node update: active_node={active_node}, task_id={task.id}"
                    )

        elif event_type == "state_update":
            # Internal event: agent state changed
            # Don't send to client - this is debugging/monitoring info
            logger.debug(
                f"State update: task_id={task.id}, data_keys={list(event_data.keys()) if isinstance(event_data, dict) else 'N/A'}"
            )

        elif event_type == "custom":
            # Custom event → send as DataPart message
            if self.task_updater:
                custom_message = Message(
                    context_id=task.context_id,
                    task_id=task.id,
                    message_id=str(uuid.uuid4()),
                    role=Role.agent,
                    parts=[Part(root=DataPart(data=event_data))],
                    metadata={
                        A2AMetadataKey.MESSAGE_TYPE.value: MessageType.EVENT.value
                    },
                )
                await self.task_updater.update_status(state="working", message=custom_message)
                logger.debug(f"Sent custom event: task_id={task.id}")

        elif event_type == "complete":
            # Execution completed
            is_interrupted = metadata.get("is_interrupted", False)
            if is_interrupted:
                # Agent is waiting for input - handle interrupt
                # First, finalize any streaming artifact
                if self.streaming_artifact_builder:
                    finalize_event = self.streaming_artifact_builder.build_meta_complete_event(
                        status_reason=ArtifactStreamingStatusReason.INTERRUPTED
                    )
                    if finalize_event:
                        await event_queue.enqueue_event(finalize_event)

                # Get interrupt information if available
                interrupt_info = metadata.get("interrupt")
                interrupt_message = None

                if interrupt_info:
                    # Extract interrupt details
                    reason = interrupt_info.get("reason", "Agent requires input")
                    prompt = interrupt_info.get("prompt")

                    # Create message with interrupt information
                    interrupt_text = prompt if prompt else reason
                    interrupt_message = Message(
                        context_id=task.context_id,
                        task_id=task.id,
                        message_id=str(uuid.uuid4()),
                        role=Role.agent,
                        parts=[Part(root=TextPart(text=interrupt_text))],
                    )

                # Determine task state (support custom types from interrupt)
                task_state = "input_required"
                if interrupt_info and "metadata" in interrupt_info:
                    custom_type = interrupt_info["metadata"].get("type")
                    if custom_type:
                        try:
                            # Try to use custom state if valid
                            from a2a.types import TaskState
                            task_state = TaskState(custom_type)
                        except:
                            pass  # Fall back to input_required

                # Update status with interrupt message
                if self.task_updater:
                    await self.task_updater.update_status(
                        state=task_state,
                        message=interrupt_message,
                        final=True,
                    )

                logger.info(
                    f"Task requires input: task_id={task.id}, "
                    f"next_steps={metadata.get('next_steps', [])}, "
                    f"interrupt={interrupt_info.get('reason') if interrupt_info else 'N/A'}"
                )
            else:
                # Agent completed successfully
                if self.task_updater:
                    await self.task_updater.complete()
                logger.info(f"Task completed: task_id={task.id}")

        elif event_type == "error":
            # Error occurred
            error_info = event_data.get("error", "Unknown error")
            logger.error(f"Execution error: task_id={task.id}, error={error_info}")

            if self.task_updater:
                await self.task_updater.failed()

        else:
            # Unknown event type
            logger.warning(
                f"Unknown execution event type: {event_type}, task_id={task.id}"
            )

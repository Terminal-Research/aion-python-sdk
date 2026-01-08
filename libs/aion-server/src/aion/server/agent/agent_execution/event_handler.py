"""Event handler for processing ExecutionEvent and publishing to A2A EventQueue.

This module provides a dedicated handler for translating ExecutionEvent objects
to A2A events, separating event handling logic from the main executor.
"""

import uuid

from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import (
    DataPart,
    Message,
    Part,
    Role,
    Task,
    TaskState,
    TextPart,
)
from aion.shared.agent.adapters import (
    CompleteEvent,
    CustomEvent,
    ErrorEvent,
    ExecutionEvent,
    InterruptEvent,
    MessageEvent,
    NodeUpdateEvent,
    StateUpdateEvent,
)
from aion.shared.context import set_langgraph_node
from aion.shared.logging import get_logger
from aion.shared.types import MessageType, A2AMetadataKey, ArtifactStreamingStatusReason

from aion.server.utils import StreamingArtifactBuilder
from .event_translator import ExecutionEventTranslator

logger = get_logger()


class ExecutionEventHandler:
    """Handler for processing ExecutionEvent and publishing to A2A EventQueue.

    This class encapsulates the logic for handling different types of execution events
    and translating them to appropriate A2A protocol events.

    Responsibilities:
    - Handle message events (streaming, final, regular)
    - Handle custom events
    - Handle internal events (node_update, state_update)
    - Handle completion events (interrupted, completed)
    - Handle error events
    """

    def __init__(
            self,
            task_updater: TaskUpdater,
            streaming_artifact_builder: StreamingArtifactBuilder,
            event_translator: ExecutionEventTranslator,
    ):
        """Initialize the event handler.

        Args:
            task_updater: TaskUpdater for publishing status updates
            streaming_artifact_builder: Builder for streaming artifacts
            event_translator: Translator for converting ExecutionEvent to A2A Message
        """
        self.task_updater = task_updater
        self.streaming_artifact_builder = streaming_artifact_builder
        self.event_translator = event_translator

    async def handle_event(
            self,
            execution_event: ExecutionEvent,
            event_queue: EventQueue,
            task: Task,
    ) -> None:
        """Handle an ExecutionEvent and publish appropriate A2A events.

        Uses type-safe isinstance checks for proper type narrowing.

        Args:
            execution_event: Event from agent execution (typed subclass)
            event_queue: Queue to publish A2A events
            task: Current task
        """
        # Route to a specific handler using isinstance for type narrowing
        if isinstance(execution_event, MessageEvent):
            await self._handle_message_event(execution_event, event_queue, task)
        elif isinstance(execution_event, NodeUpdateEvent):
            await self._handle_node_update_event(execution_event, task)
        elif isinstance(execution_event, StateUpdateEvent):
            await self._handle_state_update_event(execution_event.data, task)
        elif isinstance(execution_event, CustomEvent):
            await self._handle_custom_event(execution_event.data, task)
        elif isinstance(execution_event, InterruptEvent):
            await self._handle_interrupt_event(execution_event, event_queue, task)
        elif isinstance(execution_event, CompleteEvent):
            await self._handle_complete_event(execution_event, event_queue, task)
        elif isinstance(execution_event, ErrorEvent):
            await self._handle_error_event(execution_event, task)
        else:
            logger.warning(f"Unknown execution event type: {execution_event.event_type}")

    async def _handle_message_event(
            self,
            message_event: MessageEvent,
            event_queue: EventQueue,
            task: Task,
    ) -> None:
        """Handle message events (streaming or regular).

        Args:
            message_event: Typed MessageEvent
            event_queue: Queue to publish events
            task: Current task
        """
        if message_event.is_streaming:
            await self._handle_streaming_chunk(message_event, task)
        else:
            await self._handle_regular_message(message_event, event_queue, task)

    async def _handle_streaming_chunk(
            self,
            message_event: MessageEvent,
            task: Task,
    ) -> None:
        """Handle streaming message chunk.

        Args:
            message_event: Streaming message event
            task: Current task
        """
        chunk_text = message_event.get_text_content()
        # skip if no text content
        if not chunk_text:
            return

        logger.debug("Event: message - streaming chunk")

        if self.streaming_artifact_builder:
            artifact_event = self.streaming_artifact_builder.build_streaming_chunk_event(
                content=chunk_text,
                metadata={
                    "status": "active",
                    "status_reason": ArtifactStreamingStatusReason.CHUNK_STREAMING.value,
                }
            )
            await self.task_updater.event_queue.enqueue_event(artifact_event)

    async def _handle_regular_message(
            self,
            message_event: MessageEvent,
            event_queue: EventQueue,
            task: Task,
    ) -> None:
        """Handle regular message (not streaming).

        Sends message wrapped in TaskStatusUpdateEvent to prevent premature
        termination in consume_and_break_on_interrupt (which returns on bare Message events).

        Args:
            message_event: Typed MessageEvent (regular)
            event_queue: Queue to publish events
            task: Current task
        """
        logger.debug(f"Event: message - full message (role={message_event.role})")

        # Finalize streaming artifact before sending the final message
        if self.streaming_artifact_builder:
            finalize_event = self.streaming_artifact_builder.build_meta_complete_event(
                status_reason=ArtifactStreamingStatusReason.COMPLETE_MESSAGE
            )
            if finalize_event:
                await event_queue.enqueue_event(finalize_event)

        a2a_message = self.event_translator.translate_message_event(message_event, task)
        if a2a_message:
            await self.task_updater.update_status(
                state=TaskState.working,
                message=a2a_message
            )
        else:
            logger.warning("Failed to translate message event")

    @staticmethod
    async def _handle_node_update_event(
            node_event: NodeUpdateEvent,
            task: Task,
    ) -> None:
        """Handle node update event (internal only, not sent to a client).

        Args:
            node_event: NodeUpdateEvent with node information
            task: Current task
        """
        if node_event.node_name:
            set_langgraph_node(node_event.node_name)
            logger.debug(f"Node: {node_event.node_name}")

    @staticmethod
    async def _handle_state_update_event(
            event_data: dict,
            task: Task,
    ) -> None:
        """Handle state update event (internal only, not sent to a client).

        Args:
            event_data: State update data
            task: Current task
        """
        # State updates are internal events that don't require logging

    async def _handle_custom_event(
            self,
            event_data: dict,
            task: Task,
    ) -> None:
        """Handle custom event by publishing as a DataPart message.

        Args:
            event_data: Custom event data (may contain Pydantic models)
            task: Current task
        """
        logger.debug("Event: custom")

        if self.task_updater:
            # Ensure data is serializable (convert Pydantic models if needed)
            serializable_data = event_data

            custom_message = Message(
                context_id=task.context_id,
                task_id=task.id,
                message_id=str(uuid.uuid4()),
                role=Role.agent,
                parts=[Part(root=DataPart(data=serializable_data))],
                metadata={
                    A2AMetadataKey.MESSAGE_TYPE.value: MessageType.EVENT.value
                },
            )
            await self.task_updater.update_status(state=TaskState.working, message=custom_message)

    async def _handle_interrupt_event(
            self,
            interrupt_event: InterruptEvent,
            event_queue: EventQueue,
            task: Task,
    ) -> None:
        """Handle interrupt event (agent waiting for input).

        Supports multiple simultaneous interrupts (LangGraph 0.6.0+).
        Takes first interrupt to create user-facing message.

        Args:
            interrupt_event: Typed InterruptEvent with list of interrupts
            event_queue: Queue to publish events
            task: Current task
        """
        if self.streaming_artifact_builder:
            finalize_event = self.streaming_artifact_builder.build_meta_complete_event(
                status_reason=ArtifactStreamingStatusReason.INTERRUPTED
            )
            if finalize_event:
                await event_queue.enqueue_event(finalize_event)

        # Take first interrupt for user-facing message
        interrupt_message = None
        interrupt_id = None

        if interrupt_event.interrupts:
            interrupt_info = interrupt_event.interrupts[0]
            interrupt_id = interrupt_info.id
            interrupt_text = interrupt_info.get_prompt_text()

            interrupt_message = Message(
                context_id=task.context_id,
                task_id=task.id,
                message_id=str(uuid.uuid4()),
                role=Role.agent,
                parts=[Part(root=TextPart(text=interrupt_text))],
                metadata={
                    "interruptId": interrupt_id
                }
            )

        task_state = TaskState.input_required
        # todo add auth-required task state processing

        if self.task_updater:
            await self.task_updater.update_status(
                state=task_state,
                message=interrupt_message,
                final=False,
            )

        logger.info(
            f"Interrupted (requires input), "
            f"interrupts_count={len(interrupt_event.interrupts)}, "
            f"interrupt_id={interrupt_id or 'N/A'}"
        )

    async def _handle_complete_event(
            self,
            complete_event: CompleteEvent,
            event_queue: EventQueue,
            task: Task,
    ) -> None:
        """Handle successful completion event.

        Args:
            complete_event: Typed CompleteEvent with completion info
            event_queue: Queue to publish events
            task: Current task
        """
        if self.task_updater:
            await self.task_updater.complete()
        logger.info("Task completed successfully")

    async def _handle_error_event(
            self,
            error_event: ErrorEvent,
            task: Task,
    ) -> None:
        """Handle error event.

        Args:
            error_event: Typed ErrorEvent with error info
            task: Current task
        """
        logger.error(
            f"Execution error: {error_event.error}, "
            f"type={error_event.error_type}"
        )

        if self.task_updater:
            await self.task_updater.failed()

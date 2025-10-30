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
        logger.debug(
            f"Processing execution event: type={execution_event.event_type}, task_id={task.id}"
        )

        # Route to a specific handler using isinstance for type narrowing
        if isinstance(execution_event, MessageEvent):
            await self._handle_message_event(execution_event, event_queue, task)
        elif isinstance(execution_event, NodeUpdateEvent):
            await self._handle_node_update_event(execution_event, task)
        elif isinstance(execution_event, StateUpdateEvent):
            await self._handle_state_update_event(execution_event.data, task)
        elif isinstance(execution_event, CustomEvent):
            await self._handle_custom_event(execution_event.data, task)
        elif isinstance(execution_event, CompleteEvent):
            await self._handle_complete_event(execution_event, event_queue, task)
        elif isinstance(execution_event, ErrorEvent):
            await self._handle_error_event(execution_event, task)
        else:
            logger.warning(
                f"Unknown execution event type: {execution_event.event_type}, task_id={task.id}"
            )

    async def _handle_message_event(
            self,
            message_event: MessageEvent,
            event_queue: EventQueue,
            task: Task,
    ) -> None:
        """Handle message events (streaming, final, regular).

        Args:
            message_event: Typed MessageEvent
            event_queue: Queue to publish events
            task: Current task
        """
        is_streaming = message_event.is_streaming
        is_final = message_event.is_final

        if is_streaming:
            await self._handle_streaming_chunk(message_event.data, task)
        elif is_final:
            await self._handle_final_message(message_event, event_queue, task)
        else:
            await self._handle_regular_message(message_event, event_queue, task)

    async def _handle_streaming_chunk(
            self,
            event_data: str,
            task: Task,
    ) -> None:
        """Handle streaming message chunk.

        Args:
            event_data: Chunk content
            task: Current task
        """
        if self.streaming_artifact_builder:
            artifact_event = self.streaming_artifact_builder.build_streaming_chunk_event(
                content=event_data,
                metadata={
                    "status": "active",
                    "status_reason": ArtifactStreamingStatusReason.CHUNK_STREAMING.value,
                }
            )
            await self.task_updater.event_queue.enqueue_event(artifact_event)
            logger.debug(f"Sent streaming artifact: task_id={task.id}")

    async def _handle_final_message(
            self,
            message_event: MessageEvent,
            event_queue: EventQueue,
            task: Task,
    ) -> None:
        """Handle the final message after streaming.

        Args:
            message_event: Typed MessageEvent (final)
            event_queue: Queue to publish events
            task: Current task
        """
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

        a2a_message = self.event_translator.translate_message_event(message_event, task)
        if a2a_message and self.task_updater:
            await self.task_updater.update_status(state=TaskState.working, message=a2a_message)
            logger.debug(f"Sent final message: task_id={task.id}")

    async def _handle_regular_message(
            self,
            message_event: MessageEvent,
            event_queue: EventQueue,
            task: Task,
    ) -> None:
        """Handle regular message (not streaming).

        Args:
            message_event: Typed MessageEvent (regular)
            event_queue: Queue to publish events
            task: Current task
        """
        a2a_message = self.event_translator.translate_message_event(message_event, task)
        if a2a_message:
            await event_queue.enqueue_event(a2a_message)
        else:
            logger.warning(f"Failed to translate message event: task_id={task.id}")

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
            logger.debug(
                f"Node update: active_node={node_event.node_name}, task_id={task.id}"
            )

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
        logger.debug(
            f"State update: task_id={task.id}, "
            f"data_keys={list(event_data.keys()) if isinstance(event_data, dict) else 'N/A'}"
        )

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
            logger.debug(f"Sent custom event: task_id={task.id}")

    async def _handle_complete_event(
            self,
            complete_event: CompleteEvent,
            event_queue: EventQueue,
            task: Task,
    ) -> None:
        """Handle completion event (interrupted or completed).

        Args:
            complete_event: Typed CompleteEvent with completion info
            event_queue: Queue to publish events
            task: Current task
        """
        if complete_event.is_interrupted:
            await self._handle_interrupted_completion(complete_event, event_queue, task)
        else:
            await self._handle_successful_completion(task)

    async def _handle_interrupted_completion(
            self,
            complete_event: CompleteEvent,
            event_queue: EventQueue,
            task: Task,
    ) -> None:
        """Handle interrupted completion (agent waiting for input).

        Args:
            complete_event: Typed CompleteEvent with interrupt info
            event_queue: Queue to publish events
            task: Current task
        """
        if self.streaming_artifact_builder:
            finalize_event = self.streaming_artifact_builder.build_meta_complete_event(
                status_reason=ArtifactStreamingStatusReason.INTERRUPTED
            )
            if finalize_event:
                await event_queue.enqueue_event(finalize_event)

        interrupt_info = complete_event.interrupt
        interrupt_message = None

        if interrupt_info:
            # Use InterruptInfo object attributes (not dict)
            reason = interrupt_info.reason or "Agent requires input"
            prompt = interrupt_info.prompt
            interrupt_text = prompt if prompt else reason
            interrupt_message = Message(
                context_id=task.context_id,
                task_id=task.id,
                message_id=str(uuid.uuid4()),
                role=Role.agent,
                parts=[Part(root=TextPart(text=interrupt_text))],
            )

        task_state = "input_required"
        if interrupt_info and interrupt_info.metadata:
            custom_type = interrupt_info.metadata.get("type")
            if custom_type:
                try:
                    task_state = TaskState(custom_type)
                except ValueError:
                    pass

        if self.task_updater:
            await self.task_updater.update_status(
                state=task_state,
                message=interrupt_message,
                final=True,
            )

        logger.info(
            f"Task requires input: task_id={task.id}, "
            f"next_steps={complete_event.next_steps}, "
            f"interrupt={interrupt_info.reason if interrupt_info else 'N/A'}"
        )

    async def _handle_successful_completion(
            self,
            task: Task,
    ) -> None:
        """Handle successful completion.

        Args:
            task: Current task
        """
        if self.task_updater:
            await self.task_updater.complete()
        logger.info(f"Task completed: task_id={task.id}")

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
            f"Execution error: task_id={task.id}, "
            f"error={error_event.error}, "
            f"type={error_event.error_type}"
        )

        if self.task_updater:
            await self.task_updater.failed()

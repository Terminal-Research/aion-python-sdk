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
from aion.shared.agent.adapters import ExecutionEvent
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

        # Route to specific handler based on event type
        if event_type == "message":
            await self._handle_message_event(execution_event, event_queue, task)
        elif event_type == "node_update":
            await self._handle_node_update_event(event_data, task)
        elif event_type == "state_update":
            await self._handle_state_update_event(event_data, task)
        elif event_type == "custom":
            await self._handle_custom_event(event_data, task)
        elif event_type == "complete":
            await self._handle_complete_event(metadata, event_queue, task)
        elif event_type == "error":
            await self._handle_error_event(event_data, task)
        else:
            logger.warning(
                f"Unknown execution event type: {event_type}, task_id={task.id}"
            )

    async def _handle_message_event(
            self,
            execution_event: ExecutionEvent,
            event_queue: EventQueue,
            task: Task,
    ) -> None:
        """Handle message events (streaming, final, regular).

        Args:
            execution_event: Message event
            event_queue: Queue to publish events
            task: Current task
        """
        metadata = execution_event.metadata or {}
        event_data = execution_event.data
        is_streaming = metadata.get("is_streaming", False)
        is_final = metadata.get("is_final", False)

        if is_streaming:
            await self._handle_streaming_chunk(event_data, task)
        elif is_final:
            await self._handle_final_message(execution_event, event_queue, task)
        else:
            await self._handle_regular_message(execution_event, event_queue, task)

    async def _handle_streaming_chunk(
            self,
            event_data: str,
            task: Task,
    ) -> None:
        """Handle streaming message chunk (AIMessageChunk).

        Args:
            event_data: Chunk content
            task: Current task
        """
        # AIMessageChunk → streaming artifact
        # Note: Don't send status update here - it's redundant during streaming.
        # Status was already set to "working" on first event.
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
            execution_event: ExecutionEvent,
            event_queue: EventQueue,
            task: Task,
    ) -> None:
        """Handle final message after streaming (AIMessage).

        Args:
            execution_event: Final message event
            event_queue: Queue to publish events
            task: Current task
        """
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

    async def _handle_regular_message(
            self,
            execution_event: ExecutionEvent,
            event_queue: EventQueue,
            task: Task,
    ) -> None:
        """Handle regular message (not streaming).

        Args:
            execution_event: Message event
            event_queue: Queue to publish events
            task: Current task
        """
        a2a_message = self.event_translator.translate_message_event(execution_event, task)
        if a2a_message:
            await event_queue.enqueue_event(a2a_message)
        else:
            logger.warning(f"Failed to translate message event: task_id={task.id}")

    async def _handle_node_update_event(
            self,
            event_data: dict,
            task: Task,
    ) -> None:
        """Handle node update event (internal, not sent to client).

        Args:
            event_data: Node update data
            task: Current task
        """
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

    async def _handle_state_update_event(
            self,
            event_data: dict,
            task: Task,
    ) -> None:
        """Handle state update event (internal, not sent to client).

        Args:
            event_data: State update data
            task: Current task
        """
        # Internal event: agent state changed
        # Don't send to client - this is debugging/monitoring info
        logger.debug(
            f"State update: task_id={task.id}, "
            f"data_keys={list(event_data.keys()) if isinstance(event_data, dict) else 'N/A'}"
        )

    async def _handle_custom_event(
            self,
            event_data: dict,
            task: Task,
    ) -> None:
        """Handle custom event.

        Args:
            event_data: Custom event data
            task: Current task
        """
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

    async def _handle_complete_event(
            self,
            metadata: dict,
            event_queue: EventQueue,
            task: Task,
    ) -> None:
        """Handle completion event (interrupted or completed).

        Args:
            metadata: Event metadata with completion info
            event_queue: Queue to publish events
            task: Current task
        """
        is_interrupted = metadata.get("is_interrupted", False)

        if is_interrupted:
            await self._handle_interrupted_completion(metadata, event_queue, task)
        else:
            await self._handle_successful_completion(task)

    async def _handle_interrupted_completion(
            self,
            metadata: dict,
            event_queue: EventQueue,
            task: Task,
    ) -> None:
        """Handle interrupted completion (agent waiting for input).

        Args:
            metadata: Event metadata with interrupt info
            event_queue: Queue to publish events
            task: Current task
        """
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

    async def _handle_successful_completion(
            self,
            task: Task,
    ) -> None:
        """Handle successful completion.

        Args:
            task: Current task
        """
        # Agent completed successfully
        if self.task_updater:
            await self.task_updater.complete()
        logger.info(f"Task completed: task_id={task.id}")

    async def _handle_error_event(
            self,
            event_data: dict,
            task: Task,
    ) -> None:
        """Handle error event.

        Args:
            event_data: Error data
            task: Current task
        """
        error_info = event_data.get("error", "Unknown error")
        logger.error(f"Execution error: task_id={task.id}, error={error_info}")

        if self.task_updater:
            await self.task_updater.failed()

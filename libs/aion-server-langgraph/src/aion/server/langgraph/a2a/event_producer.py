"""Utility functions for creating and handling A2A messages."""

import logging
import uuid

from typing import Any, Sequence, Optional

from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState, Message, Part, Role, TextPart, DataPart, Task, TaskArtifactUpdateEvent
from a2a.server.events import EventQueue
from langchain_core.messages import BaseMessage, AIMessageChunk, AIMessage
from langgraph.types import Interrupt, StateSnapshot

from aion.server.types import MessageType, A2AEventType, A2AMetadataKey, ArtifactStreamingStatusReason
from aion.server.utils import StreamingArtifactBuilder

logger = logging.getLogger(__name__)

class LanggraphA2AEventProducer:
    """Event producer for converting LangGraph events to A2A task events.

    This class bridges LangGraph's event system with A2A's task event system,
    handling streaming messages, state updates, and task completion events.
    It converts LangGraph events into appropriate A2A TaskStatusUpdateEvent
    and TaskArtifactUpdateEvent objects.
    """

    def __init__(self, event_queue: EventQueue, task: Task):
        """Initialize the LangGraph A2A Event Producer.

        Args:
            event_queue: The event queue for publishing A2A events
            task: The task instance associated with this producer
        """
        self.task = task
        self.event_queue = event_queue
        self.updater = TaskUpdater(event_queue, task.id, task.context_id)

    @property
    def streaming_artifact_builder(self):
        if hasattr(self, '_streaming_artifact_builder'):
            return self._streaming_artifact_builder

        self._streaming_artifact_builder = StreamingArtifactBuilder(task=self.task)
        return self._streaming_artifact_builder

    async def handle_event(
        self,
        event_type: str,
        event: Any,
    ):
        """Handle incoming LangGraph events and convert them to A2A events.

        Routes different types of LangGraph events to appropriate handlers
        that convert them into A2A TaskStatusUpdateEvent or TaskArtifactUpdateEvent.

        Args:
            event_type: Type of LangGraph event (messages, values, custom, interrupt, complete)
            event: The event data payload

        Raises:
            ValueError: If an unhandled event type is encountered
        """
        if event_type == A2AEventType.MESSAGES.value:
            await self._stream_message(event)
        elif event_type == A2AEventType.VALUES.value:
            await self._emit_langgraph_values(event)
        elif event_type == A2AEventType.CUSTOM.value:
            await self._emit_langgraph_event(event)
        elif event_type == A2AEventType.INTERRUPT.value:
            await self._handle_interrupt(event)
        elif event_type == A2AEventType.COMPLETE.value:
            await self._handle_complete(event)
        else:
            raise ValueError(
                f"Unhandled event. Event Type: {event_type}, Event: {event}"
            )

    async def update_status_working(self, force: bool = False):
        """Update task status to working state."""
        if self.task.status.state == TaskState.working and not force:
            return

        await self.updater.update_status(state=TaskState.working)

    async def _handle_complete(self, event: StateSnapshot):
        """Handle task completion event from LangGraph."""
        await self.updater.complete()

    async def _handle_interrupt(self, interrupts: Sequence[Interrupt]):
        """Handle LangGraph interrupt events.

        Processes interrupts from LangGraph execution and updates task status
        to input_required with the interrupt message.

        Args:
            interrupts: Sequence of interrupt objects from LangGraph
        """
        interruption = interrupts[0] if len(interrupts) else None
        if not interruption:
            return

        await self.add_stream_artefact(
            self.streaming_artifact_builder.build_meta_complete_event(
                status_reason=ArtifactStreamingStatusReason.INTERRUPTED
            )
        )

        await self.updater.update_status(
            TaskState.input_required,
            message=Message(
                context_id=self.task.context_id,
                task_id=self.task.id,
                message_id=str(uuid.uuid4()),
                role=Role.agent,
                parts=[Part(root=TextPart(text=interruption.value))],
            ),
            final=True,
        )

    async def _stream_message(self, langgraph_message: BaseMessage):
        """Stream message chunks from LangGraph as A2A status updates.

        Handles streaming message chunks from LangGraph by sending delta updates
        to the client. Only processes AIMessageChunk instances for streaming updates.

        Args:
            langgraph_message (BaseMessage): The message object from LangGraph,
                expected to be an AIMessageChunk for streaming content
        """
        await self.add_stream_artefact(
            self.streaming_artifact_builder.build_from_langgraph_message(langgraph_message=langgraph_message)
        )

        if isinstance(langgraph_message, AIMessageChunk):
            await self.update_status_working()
            return

        if isinstance(langgraph_message, AIMessage):
            await self.updater.update_status(
                state=TaskState.working,
                message=Message(
                    context_id=self.task.context_id,
                    task_id=self.task.id,
                    message_id=str(uuid.uuid4()),
                    role=Role.agent,
                    parts=[Part(root=TextPart(text=langgraph_message.content))]
                ),
            )
            return

    async def _emit_langgraph_event(self, event: dict):
        """Emit custom LangGraph events as A2A status updates.

        Processes custom events from LangGraph and forwards them as
        TaskStatusUpdateEvent with event metadata for client consumption.

        Args:
            event: Dictionary containing custom event data from LangGraph
        """
        # Transform custom_event field to event field for A2A compatibility
        emit_event = {k: v for k, v in event.items() if k != "custom_event"}
        if "custom_event" in event:
            emit_event["event"] = event["custom_event"]

        await self.updater.update_status(
            state=TaskState.working,
            message=Message(
                context_id=self.task.context_id,
                task_id=self.task.id,
                message_id=str(uuid.uuid4()),
                role=Role.agent,
                parts=[Part(root=DataPart(data=emit_event))],
                metadata={
                    A2AMetadataKey.MESSAGE_TYPE.value: MessageType.EVENT.value
                },
            ),
        )

    async def _emit_langgraph_values(self, event: dict):
        """Emit LangGraph state values as A2A status updates.

        Processes state value updates from LangGraph and forwards them as
        TaskStatusUpdateEvent with langgraph_values metadata for debugging
        and monitoring purposes.

        Args:
            event: Dictionary containing state values from LangGraph
        """
        return await self.update_status_working()

    async def add_stream_artefact(self, event: Optional[TaskArtifactUpdateEvent] = None):
        """
        Add a task artifact update event to the streaming queue.

        Args:
            event: Optional task artifact update event to be queued for processing.
                   If None, the method returns without performing any action.
        """
        if not event:
            return

        await self.event_queue.enqueue_event(event)
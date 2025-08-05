"""Utility functions for creating and handling A2A messages."""

import logging
import uuid

from typing import Any, Sequence

from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState, Message, Part, Role, TextPart, DataPart, Task
from a2a.server.events import EventQueue
from langchain_core.messages import BaseMessage, AIMessageChunk, AIMessage
from langgraph.types import Interrupt, StateSnapshot

from aion.server.types import ArtifactName, MessageType, A2AEventType, A2AMetadataKey

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

    async def update_status_working(self):
        """Update task status to working state."""
        await self.updater.update_status(state=TaskState.working)

    async def _handle_complete(self, event: StateSnapshot):
        """Handle task completion event from LangGraph.

        Processes the completion state snapshot, extracts the final message,
        creates an artifact for the result, and completes the task with
        the final message or without a message if none is available.

        Args:
            event (StateSnapshot): The completion state snapshot from LangGraph
                containing the final state values and messages
        """
        last_message_parts = None
        if event.values and len(event.values["messages"]):
            last_message = event.values["messages"][-1]
            if last_message and isinstance(last_message, AIMessage):
                last_message_parts = [Part(root=TextPart(text=last_message.content))]
                await self.updater.add_artifact(
                    parts=last_message_parts,
                    artifact_id=str(uuid.uuid4()),
                    name=ArtifactName.MESSAGE_RESULT.value,
                    append=False,
                    last_chunk=True,
                )

        if last_message_parts:
            await self.updater.complete(
                message=Message(
                    contextId=self.task.context_id,
                    taskId=self.task.id,
                    messageId=str(uuid.uuid4()),
                    role=Role.agent,
                    parts=last_message_parts
                ),
            )
        else:
            await self.updater.complete()

    async def _handle_interrupt(self, interrupts: Sequence[Interrupt]):
        """Handle LangGraph interrupt events.

        Processes interrupts from LangGraph execution and updates task status
        to input_required with the interrupt message.

        Args:
            interrupts: Sequence of interrupt objects from LangGraph
        """
        interrupt = interrupts[0] if interrupts else None
        if not interrupt:
            return

        # todo add more complex processing of interruption type
        if isinstance(interrupt.value, dict):
            interruption_type_value = interrupt.value.get("type", None)
            if interruption_type_value:
                try:
                    task_state = TaskState(interruption_type_value)
                except:
                    task_state = TaskState.input_required
            else:
                task_state = TaskState.input_required

            interrupt_message = interrupt.value.get("message", None) or str(interrupt.value)
        else:
            task_state = TaskState.input_required
            interrupt_message = str(interrupt.value)

        await self.updater.update_status(
            task_state,
            message=Message(
                contextId=self.task.context_id,
                taskId=self.task.id,
                messageId=str(uuid.uuid4()),
                role=Role.agent,
                parts=[Part(root=TextPart(text=interrupt_message))],
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
        if isinstance(langgraph_message, AIMessageChunk):
            # Stream the delta via status update with message
            await self.updater.update_status(
                state=TaskState.working,
                message=Message(
                    contextId=self.task.context_id,
                    taskId=self.task.id,
                    messageId=str(uuid.uuid4()),
                    role=Role.agent,
                    parts=[Part(root=TextPart(text=langgraph_message.content))],
                    metadata={
                        A2AMetadataKey.MESSAGE_TYPE.value: MessageType.STREAM_DELTA.value
                    },
                ),
            )

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
                contextId=self.task.context_id,
                taskId=self.task.id,
                messageId=str(uuid.uuid4()),
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
        await self.updater.update_status(
            state=TaskState.working,
            message=Message(
                contextId=self.task.context_id,
                taskId=self.task.id,
                messageId=str(uuid.uuid4()),
                role=Role.agent,
                parts=[Part(root=DataPart(data=event))],
                metadata={
                    A2AMetadataKey.MESSAGE_TYPE.value: MessageType.LANGRAPH_VALUES.value
                },
            ),
        )

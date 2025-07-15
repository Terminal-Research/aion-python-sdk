"""Utility functions for creating and handling A2A messages."""

import logging
import uuid

from typing import Any, Sequence

from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState, Message, Part, Role, TextPart, DataPart, Task
from a2a.server.events import EventQueue
from langchain_core.messages import BaseMessage, AIMessageChunk, AIMessage
from langgraph.types import Interrupt, StateSnapshot

logger = logging.getLogger(__name__)

class LanggraphA2AEventProducer:

    ARTIFACT_NAME_MESSAGE_STREAMING = "message_streaming"
    ARTIFACT_NAME_MESSAGE_RESULT = "message_result"

    def __init__(self, event_queue: EventQueue, task: Task):
        self.task = task
        self.event_queue = event_queue
        self.updater = TaskUpdater(event_queue, task.id, task.contextId)
        self._streaming_result_artifact_id = None

    async def handle_event(
        self,
        event_type: str,
        event: Any,
    ):
        if event_type == "messages":
            await self._stream_message(event)
        elif event_type == "values":
            await self._emit_langgraph_values(event)
        elif event_type == "custom":
            await self._emit_langgraph_event(event)
        elif event_type == "interrupt":
            await self._handle_interrupt(event)
        elif event_type == "complete":
            await self._handle_complete(event)
        else:
            raise ValueError(
                f"Unhandled event. Event Type: {event_type}, Event: {event}"
            )

    async def update_status_working(self):
        await self.updater.update_status(state=TaskState.working)
        
    async def _handle_complete(self, event: StateSnapshot):
        last_message_parts = None
        if event.values and len(event.values["messages"]):
            last_message = event.values["messages"][-1]
            if last_message and isinstance(last_message, AIMessage):
                last_message_parts = [Part(root=TextPart(text=last_message.content))]
                await self.updater.add_artifact(
                    parts=last_message_parts,
                    artifact_id=str(uuid.uuid4()),
                    name=self.ARTIFACT_NAME_MESSAGE_RESULT,
                    append=False,
                    last_chunk=True,
                )
                
        if last_message_parts:
            await self.updater.complete(
                message=Message(
                    contextId=self.task.contextId,
                    taskId=self.task.id,
                    messageId=str(uuid.uuid4()),
                    role=Role.agent,
                    parts=last_message_parts
                ),
            )
        else:
            await self.updater.complete()

    async def _handle_interrupt(self, interrupts: Sequence[Interrupt]):
        if len(interrupts):
          await self.updater.update_status(
              TaskState.input_required,
              message=Message(
                  contextId=self.task.contextId,
                  taskId=self.task.id,
                  messageId=str(uuid.uuid4()),
                  role=Role.agent,
                  parts=[Part(root=TextPart(text=interrupts[0].value))],
              ),
              final=True,
          )

    async def _stream_message(self, langgraph_message: BaseMessage):
        if isinstance(langgraph_message, AIMessageChunk):
            append = self._streaming_result_artifact_id != None
            if not append:
                self._streaming_result_artifact_id = str(uuid.uuid4())

            await self.updater.add_artifact(
                parts=[Part(root=TextPart(text=langgraph_message.content))],
                artifact_id=self._streaming_result_artifact_id,
                name=self.ARTIFACT_NAME_MESSAGE_STREAMING,
                append=append,
                last_chunk=False,
            )

    async def _emit_langgraph_event(self, event: dict):
        emit_event = {k: v for k, v in event.items() if k != "custom_event"}
        if "custom_event" in event:
            emit_event["event"] = event["custom_event"]

        await self.updater.update_status(
            state=TaskState.working,
            message=Message(
                contextId=self.task.contextId,
                taskId=self.task.id,
                messageId=str(uuid.uuid4()),
                role=Role.agent,
                parts=[Part(root=DataPart(data=emit_event))],
                metadata={"aion:message_type": "event"},
            ),
        )

    async def _emit_langgraph_values(self, event: dict):
        await self.updater.update_status(
            state=TaskState.working,
            message=Message(
                contextId=self.task.contextId,
                taskId=self.task.id,
                messageId=str(uuid.uuid4()),
                role=Role.agent,
                parts=[Part(root=DataPart(data=event))],
                metadata={"aion:message_type": "langgraph_values"},
            ),
        )

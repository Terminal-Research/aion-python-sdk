"""Utility functions for creating and handling A2A messages."""

from a2a.server.events import EventQueue
from aion.server.langgraph.a2a.tasks import AionTaskUpdater
from a2a.types import TaskState, Message, Part, Role, TextPart, DataPart, Task
from langchain_core.messages import BaseMessage, AIMessageChunk
import uuid
from typing import Any, Sequence
from langgraph.types import Interrupt

class LanggraphA2AEventProducer:

    ARTIFACT_NAME_MESSAGE_STREAMING = "message_streaming"
    ARTIFACT_NAME_MESSAGE_RESULT = "message_result"

    def __init__(self, event_queue: EventQueue, task: Task):
        self.task = task
        self.event_queue = event_queue
        self.updater = AionTaskUpdater(event_queue, task.id, task.contextId)
        self._streaming_result_artifact_id = None

    def handle_event(
        self,
        event_type: str,
        event: Any,
        is_task_complete: bool,
    ):
        if event_type == "messages":
            self._stream_message(event)
        elif event_type == "values":
            self._emit_langgraph_values(event)
        elif event_type == "custom":
            self._emit_langgraph_event(event)
        elif event_type == "interrupt":
            self._handle_interrupt(event)
        else:
            raise ValueError(
                f"Unhandled event. Event Type: {event_type}, Event: {event}"
            )

    def update_status_working(self):
        self.updater.update_status(
            state=TaskState.working,
        )

    def _handle_interrupt(self, interrupts: Sequence[Interrupt]):
        if len(interrupts):
          self.updater.update_status(
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

    def _stream_message(self, langgraph_message: BaseMessage):
        if isinstance(langgraph_message, AIMessageChunk):
            append = self._streaming_result_artifact_id != None
            if not append:
                self._streaming_result_artifact_id = str(uuid.uuid4())

            self.updater.add_artifact(
                parts=[Part(root=TextPart(text=langgraph_message.content))],
                artifact_id=self._streaming_result_artifact_id,
                name=self.ARTIFACT_NAME_MESSAGE_STREAMING,
                append=append,
                last_chunk=False,
            )

    def _emit_langgraph_event(self, event: dict):
        emit_event = {k: v for k, v in event.items() if k != "custom_event"}
        if "custom_event" in event:
            emit_event["event"] = event["custom_event"]

        self.updater.update_status(
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

    def _emit_langgraph_values(self, event: dict):
        self.updater.update_status(
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

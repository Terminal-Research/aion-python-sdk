"""Utility functions for creating and handling A2A messages."""

from a2a.server.events import EventQueue
from aion.server.langgraph.a2a.tasks import AionTaskUpdater
from a2a.types import TaskState, Message, Part, Role, TextPart, Task
from langchain_core.messages import BaseMessage, AIMessageChunk

class LanggraphA2AEventProducer:
    def __init__(self, event_queue: EventQueue, task: Task):
        self.task = task
        self.event_queue = event_queue
        self.updater = AionTaskUpdater(event_queue, task.id, task.contextId)

    def update_status_working(self):
      self.updater.update_status(
          state=TaskState.working,
      )

    def handle_message(self, langgraph_message: BaseMessage):
      if isinstance(langgraph_message, AIMessageChunk):
        message = Message(
            role=Role.agent,
            parts=[Part(root=TextPart(text=langgraph_message.content))],
            messageId=str(uuid.uuid4()),
            taskId=self.task.id,
            contextId=self.task.contextId,
            metadata={
              "aion:message_type": "partial",
              "aion:append": True
            }
        )
        self.event_queue.enqueue_event(message)
      
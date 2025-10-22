from typing import List

from aion.shared.base import A2ABaseModel
from a2a.types import Message, Artifact, TaskState

__all__ = [
    "Conversation",
    "ContextsList",
    "ConversationTaskStatus",
]

from pydantic import RootModel, Field, ConfigDict


class ConversationTaskStatus(A2ABaseModel):
    state: TaskState
    """
    The current state of the task's lifecycle.
    """


class Conversation(A2ABaseModel):
    """Data model for conversation representation"""
    context_id: str
    """
    Unique identifier for the conversation context.
    """
    history: List[Message] = Field(default_factory=list)
    """
    List of messages in the conversation history.
    """
    artifacts: List[Artifact] = Field(default_factory=list)
    """
    List of artifacts associated with the conversation.
    """
    status: ConversationTaskStatus
    """
    Current status of the conversation.
    """


class ContextsList(RootModel[List[str]]):
    """A list of context strings for LangGraph agent communication."""
    root: List[str]

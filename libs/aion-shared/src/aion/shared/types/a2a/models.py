from typing import List, Dict

from a2a.types import Message, Artifact, TaskState
from pydantic import RootModel, Field

from aion.shared.base import A2ABaseModel

__all__ = [
    "Conversation",
    "ContextsList",
    "ConversationTaskStatus",
    "RootManifest",
]


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


class RootManifest(A2ABaseModel):
    """Data model for root manifest representation.

    Represents the root-level service manifest that defines the API version,
    service name, and available agent endpoints for A2A communication.
    """
    api_version: str = Field(
        ...,
        description="Manifest API version."
    )
    """
    Version of the manifest API specification being used.
    """
    name: str = Field(
        ...,
        description="Service name"
    )
    """
    Human-readable name of the service.
    """
    endpoints: Dict[str, str] = Field(
        default_factory=dict,
        description="A map of agent identifiers to relative paths"
    )
    """
    Mapping of agent identifiers to their corresponding endpoint paths.
    """
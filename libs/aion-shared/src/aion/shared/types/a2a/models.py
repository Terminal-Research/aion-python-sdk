from typing import Any, List, Dict, Optional, TYPE_CHECKING

from a2a.types import Message, Artifact, Task, TaskState
from pydantic import ConfigDict, RootModel, Field

from aion.shared.a2a import A2ABaseModel

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext

__all__ = [
    "A2AInbox",
    "Conversation",
    "ContextsList",
    "ConversationTaskStatus",
    "A2AManifest",
]


class A2AInbox(A2ABaseModel):
    """Server-populated input envelope for graphs that opt into A2A.

    Graphs declare `a2a_inbox: A2AInbox` in their state schema to receive
    a snapshot of the current A2A context at invocation time.  All fields are
    defensive copies — mutating them does not affect server state.
    """

    model_config = ConfigDict(frozen=True)

    task: Optional[Task] = None
    """
    Current A2A Task.  None before a task has been created.
    """
    message: Optional[Message] = None
    """
    Inbound A2A Message that triggered this execution
    """
    metadata: dict[str, Any] = Field(default_factory=dict)
    """
    Request-level metadata (e.g. `aion:network`)
    """

    @classmethod
    def from_request_context(cls, context: "RequestContext") -> "A2AInbox":
        """Build a frozen A2AInbox snapshot from an A2A RequestContext."""
        return cls(
            task=context.current_task.model_copy(deep=True) if context.current_task else None,
            message=context.message.model_copy(deep=True) if context.message else None,
            metadata=dict(context.metadata) if context.metadata else {},
        )


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


class A2AManifest(A2ABaseModel):
    """Data model for root manifest representation.

    Represents the root-level service manifest that defines the API version,
    service name, and available agent endpoints for A2A communication.
    """
    api_version: str = Field(
        ...,
        description="Manifest API version."
    )
    name: str = Field(
        ...,
        description="Service name"
    )
    endpoints: Dict[str, str] = Field(
        default_factory=dict,
        description="A map of agent identifiers to relative paths"
    )

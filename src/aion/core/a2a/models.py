"""Core A2A data models for agent execution and task management.

Defines structures for agent inboxes, outboxes, conversations, and task status
tracking within the Agent-to-Agent (A2A) protocol layer.
"""

import copy
from datetime import datetime, timezone
from typing import Any, List, Dict, Optional, TYPE_CHECKING

from a2a.types import Message, Artifact, Task, TaskState, TaskStatus
from google.protobuf.json_format import MessageToDict
from pydantic import ConfigDict, RootModel, Field, field_serializer, model_serializer, model_validator

from aion.core.a2a import A2ABaseModel
from aion.core.utils.pydantic import Protobuf

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext

__all__ = [
    "A2AInbox",
    "A2AOutbox",
    "ContextArtifact",
    "ContextSummary",
    "ContextSummaryList",
    "ContextView",
    "DeleteContextResult",
    "A2AManifest",
]


class A2AInbox(A2ABaseModel):
    """Server-populated input envelope for agents that opt into A2A.

    The server builds one per invocation and hands it over on
    `AionRuntimeContext.inbox`, so an agent reaches it the way it reaches the
    rest of that context: a LangGraph node through
    `Runtime[AionRuntimeContext]`, an ADK agent through
    `ctx.aion_runtime_context`.  It is a snapshot of the A2A context as it
    stood at invocation time, and all fields are defensive copies — mutating
    them does not affect server state.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    task: Optional[Protobuf[Task]] = None
    """
    Current A2A Task.  None before a task has been created.
    """
    message: Optional[Protobuf[Message]] = None
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
            task=copy.deepcopy(context.current_task) if context.current_task else None,
            message=copy.deepcopy(context.message) if context.message else None,
            metadata=dict(context.metadata) if context.metadata else {},
        )


class A2AOutbox(A2ABaseModel):
    """Serializable wrapper for the agent's outgoing A2A Task or Message.

    Graphs set `a2a_outbox` in their state to return a Task or Message at the
    end of execution. It answers the run that wrote it: the saved state keeps
    the value afterwards, and later turns of the same context do not apply it
    again. Wrapping in a Pydantic model with Protobuf annotations ensures
    LangGraph's checkpoint saver can serialize the state.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    task: Optional[Protobuf[Task]] = Field(
        default=None,
        description="Outbound A2A Task to return at the end of agent execution.",
    )
    message: Optional[Protobuf[Message]] = Field(
        default=None,
        description="Outbound A2A Message to return at the end of agent execution.",
    )


class ContextSummary(A2ABaseModel):
    """One entry of a ``GetContexts`` result: a caller-visible context by its latest activity."""

    context_id: str
    """
    Opaque caller-visible context identifier.
    """
    title: Optional[str] = None
    """
    Generated current subject. The SDK server generates none, so it is ``None``.
    """
    summary: Optional[str] = None
    """
    Generated conversation summary. The SDK server generates none, so it is ``None``.
    """
    last_activity_at: datetime
    """
    Latest task activity in the context, in UTC.
    """

    @field_serializer("last_activity_at")
    def serialize_last_activity_at(self, value: datetime) -> str:
        """Serialize as an RFC 3339 UTC timestamp with millisecond precision."""
        return _rfc3339_utc(value)


class ContextSummaryList(RootModel[List[ContextSummary]]):
    """The ``GetContexts`` result: a raw array of context summaries, most recent first."""

    root: List[ContextSummary] = Field(default_factory=list)


class ContextArtifact(A2ABaseModel):
    """An artifact of a context, qualified by the task that produced it.

    Serialized as the A2A artifact's own fields plus ``taskId``: artifact
    identity within a context is the pair ``(taskId, artifactId)``, since two
    tasks can each produce an artifact with the same ID.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    task_id: str
    """
    Caller-facing identifier of the task that produced the artifact.
    """
    artifact: Protobuf[Artifact]
    """
    The A2A artifact.
    """

    @model_validator(mode="before")
    @classmethod
    def _split_flat_payload(cls, data: Any) -> Any:
        """Accept the flat wire shape: artifact fields next to ``taskId``."""
        if isinstance(data, dict) and "artifact" not in data:
            fields = dict(data)
            task_id = fields.pop("taskId", fields.pop("task_id", None))
            return {"task_id": task_id, "artifact": fields}
        return data

    @model_serializer(mode="plain")
    def _serialize_flat(self) -> dict[str, Any]:
        return {"taskId": self.task_id, **MessageToDict(self.artifact)}


class ContextView(A2ABaseModel):
    """The ``GetContext`` result: one context's messages, recent artifacts and latest status.

    History combines the messages of every task in the context in
    chronological order; it is not a list of tasks.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    context_id: str
    """
    Caller-visible context identifier.
    """
    title: Optional[str] = None
    """
    Generated current subject. The SDK server generates none, so it is ``None``.
    """
    summary: Optional[str] = None
    """
    Generated conversation summary. The SDK server generates none, so it is ``None``.
    """
    history: List[Protobuf[Message]] = Field(default_factory=list)
    """
    Chronological messages of the selected history page.
    """
    artifacts: List[ContextArtifact] = Field(default_factory=list)
    """
    Up to 50 latest durable artifacts, newest first.
    """
    status: Protobuf[TaskStatus]
    """
    Status of the context's most recently active task.
    """
    last_activity_at: datetime
    """
    Latest task activity in the context, in UTC.
    """

    @field_serializer("status")
    def serialize_status(self, value: TaskStatus) -> dict[str, Any]:
        """Serialize the status with its ``state`` always present."""
        serialized = MessageToDict(value)
        serialized.setdefault("state", TaskState.Name(value.state))
        return serialized

    @field_serializer("last_activity_at")
    def serialize_last_activity_at(self, value: datetime) -> str:
        """Serialize as an RFC 3339 UTC timestamp with millisecond precision."""
        return _rfc3339_utc(value)


class DeleteContextResult(A2ABaseModel):
    """The ``DeleteContext`` result."""

    context_id: str
    """
    The context the request named.
    """


def _rfc3339_utc(value: datetime) -> str:
    """Format ``value`` as ``YYYY-MM-DDTHH:MM:SS.mmmZ``; a naive value is taken as UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


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

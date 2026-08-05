"""Enumeration types for Agent-to-Agent (A2A) communication.

Defines constant identifiers and types used across the A2A protocol layer.
"""

from enum import Enum

__all__ = [
    "MessageType",
    "ArtifactId",
    "ArtifactName",
    "A2AEventType",
    "A2AMetadataKey",
    "ArtifactStreamingStatus",
    "ArtifactStreamingStatusReason",
    "TaskSettlementReason",
]


class MessageType(str, Enum):
    """Types of messages that can be processed in the system."""
    MESSAGE = "message"
    EVENT = "event"
    LANGRAPH_VALUES = "langraph_values"


class ArtifactId(str, Enum):
    """Artifact IDs used in A2A message headers."""
    STREAM_DELTA = "aion:stream-delta"
    THINKING_DELTA = "aion:thinking-delta"
    EPHEMERAL_MESSAGE = "aion:ephemeral-message"
    REACTION = "aion:reaction"


class ArtifactName(str, Enum):
    """Named artifacts that can be created and referenced."""
    MESSAGE_RESULT = "Message Result"
    STREAM_DELTA = "Stream Delta"
    THINKING_DELTA = "Thinking Delta"
    EPHEMERAL_MESSAGE = "Ephemeral Message"
    REACTION = "Reaction"
    OUTPUT_FILE = "Output File"


class A2AEventType(str, Enum):
    """Event types for Agent-to-Agent (A2A) communication."""
    MESSAGES = "messages"
    VALUES = "values"
    CUSTOM = "custom"
    UPDATES = "updates"
    INTERRUPT = "interrupt"
    COMPLETE = "complete"


class A2AMetadataKey(str, Enum):
    """Metadata keys used in A2A message headers (metadata)."""
    MESSAGE_TYPE = "aion:messageType"
    SENDER_ID = "aion:senderId"
    SIGNATURE = "aion:signature"
    NETWORK = "aion:network"
    DISTRIBUTION = "aion:distribution"
    SETTLED_REASON = "aion:settledReason"


class TaskSettlementReason(str, Enum):
    """Why the server, rather than the agent, decided a task's final state.

    Written to `Task.metadata` under `A2AMetadataKey.SETTLED_REASON` so a client
    can tell a state the agent declared from one the server had to supply. The
    reason deliberately lives in metadata rather than in `status.message`: a
    status message is promoted into task history on the following turn, which
    would show a resumed agent words it never produced.

    A state is only ever supplied where the execution is known to be gone. A
    stream that merely ends — because the client disconnected or the turn
    produced no outcome — is left alone: the execution outlives the subscriber
    and records the truth itself.
    """

    SERVER_SHUTDOWN = "server_shutdown"
    """The server stopped while the task was still running.

    Shutdown cancels the execution, so nothing is left to record an outcome and
    the task would otherwise stay active in the store forever. It is settled as
    `INPUT_REQUIRED`: non-active, so the task is no longer presented as running,
    yet non-terminal, so it can still be resumed.
    """


class ArtifactStreamingStatus(str, Enum):
    """Enumeration representing the current status of artifact streaming."""

    FINALIZED = "finalized"
    """Artifact streaming has been completed and finalized."""

    ACTIVE = "active"
    """Artifact streaming is currently in progress."""


class ArtifactStreamingStatusReason(str, Enum):
    """Enumeration representing the reason for the current artifact streaming status."""

    INTERRUPTED = "interrupted"
    """Streaming was interrupted before completion."""

    COMPLETE_MESSAGE = "complete_message"
    """Streaming completed with a AIMessage."""

    COMPLETE_TASK = "complete_task"
    """Streaming completed with a Task status update."""

    CHUNK_STREAMING = "chunk_streaming"
    """Currently streaming data in chunks."""

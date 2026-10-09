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
    ROUTED_EXTENSION = "aion:routedExtension"


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

    A reason settles the task terminally — never with a resumable state.
    A non-terminal state would claim the opposite of what happened: the
    server auto-adopts the last interrupted task of a context for a message
    that arrives without a task id, so a resumable settlement would swallow
    the next message of the conversation as the answer to a question no agent
    asked. The conversation continues regardless — the context keeps the
    history and the next message opens a fresh task on it.

    A settled task is `FAILED`: the run stopped without an outcome and nothing
    can carry it on; see `aion.server.tasks.settlement.settled_task`.
    """

    SERVER_SHUTDOWN = "server_shutdown"
    """The server stopped while the task was still running.

    Shutdown cancels the execution, so nothing is left to record an outcome and
    the task would otherwise stay active in the store forever. The shutting-down
    process settles the task itself, as its last write to it.
    """

    @property
    def description(self) -> str:
        """A one-sentence, human-readable statement of this reason.

        Provided so a client rendering a settled task does not have to invent
        its own wording for a token this package defines — and so the prose
        has exactly one home, next to the token it explains, rather than one
        copy per surface that displays it.

        Deliberately *not* written into `status.message` by the settlement
        itself: a status message is promoted into task history on the next
        turn, which would show a resumed agent words it never produced (see
        this class's docstring). Whoever displays the task is free to show
        this; the task record stays a record of what the agent said.
        """
        return _SETTLEMENT_DESCRIPTIONS[self]


# Kept beside the enum rather than inside it: a plain dict attribute in an Enum
# body would be taken for another member.
_SETTLEMENT_DESCRIPTIONS = {
    TaskSettlementReason.SERVER_SHUTDOWN: (
        "The server was shut down while this task was still running, so the "
        "task was stopped without finishing."
    ),
}


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

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

    Every reason settles the task terminally — never with a resumable state.
    A non-terminal state would claim the opposite of what happened: the
    server auto-adopts the last interrupted task of a context for a message
    that arrives without a task id, so a resumable settlement would swallow
    the next message of the conversation as the answer to a question no agent
    asked. The conversation continues regardless — the context keeps the
    history and the next message opens a fresh task on it.

    Most reasons settle as `FAILED`: the run stopped without an outcome and
    nothing can carry it on. `CANCEL_REQUESTED` and `CANCEL_TIMEOUT` are the
    exception — they settle as `CANCELED`, because a cancellation someone
    asked for is not a failure, it is the outcome that was requested; see
    `aion.server.tasks.settlement.settled_task`.
    """

    SERVER_SHUTDOWN = "server_shutdown"
    """The server stopped while the task was still running.

    Shutdown cancels the execution, so nothing is left to record an outcome and
    the task would otherwise stay active in the store forever.
    """

    SERVER_RESTART = "server_restart"
    """A previous server process died while the task was still running.

    A hard kill — SIGKILL, OOM, a lost machine — leaves no chance to settle
    anything: the process is gone before shutdown runs, so the task keeps the
    active state it had with nothing alive to advance it. The next start finds
    it in the store and settles it as a graceful shutdown would have; only the
    reason tells the two apart.
    """

    LEASE_EXPIRED = "lease_expired"
    """This task's ownership lease expired before it was renewed.

    Reported when a task is reclaimed by timeout rather than found gone at
    startup, so it carries a weaker guarantee than `SERVER_RESTART`: the
    previous owner is presumed gone, not confirmed gone.
    """

    CANCEL_REQUESTED = "cancel_requested"
    """A cancellation was requested and the owner's lease expired before it acted on it.

    The mark left by `BaseTaskStore.request_cancellation` -
    `task_claims.cancel_requested_at` - outlives the owner only for as long as
    the claim itself does. When the reaper reclaims an expired lease that
    still carries the mark, the request survives it by exactly this one
    settlement: closing the task as `CANCELED`, honoring what was asked,
    rather than `FAILED`, which would report an ordinary lost-owner outcome
    for a task whose owner may simply have died mid-cancellation.
    """

    CANCEL_TIMEOUT = "cancel_timeout"
    """A cancellation was requested, but the owner did not honor it in time.

    Distinct from `CANCEL_REQUESTED`: here the owner's lease never expired -
    it kept renewing normally, meaning the process is alive but its
    cancellation handling is stuck unwinding past its own grace period. The
    reaper forces the task closed regardless of the live lease so the request
    cannot hang forever; see `ClaimReaper` and
    `aion.server.tasks.ownership.config.LeaseSettings.cancel_grace_seconds`.
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
    TaskSettlementReason.SERVER_RESTART: (
        "The server process running this task stopped unexpectedly, so the "
        "task was stopped without finishing."
    ),
    TaskSettlementReason.LEASE_EXPIRED: (
        "The worker running this task stopped reporting, so the task was "
        "closed without finishing."
    ),
    TaskSettlementReason.CANCEL_REQUESTED: (
        "This task was cancelled as requested; the worker running it stopped "
        "before it could report the cancellation itself."
    ),
    TaskSettlementReason.CANCEL_TIMEOUT: (
        "This task was cancelled as requested; the worker running it did not "
        "stop in time and was closed out."
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

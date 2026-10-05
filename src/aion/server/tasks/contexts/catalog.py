"""What the Context extension needs from storage, and the shapes it gets back.

A context is visible to a caller exactly while the caller is **bound** to it -
see ``aion.server.tasks.admission``. Visibility decides everything the
extension does:

- ``GetContexts`` lists the caller's bound contexts, most recent first;
- ``GetContext`` reads a bound context - every participant's messages, not
  only the caller's own - and treats any other context as absent;
- ``DeleteContext`` removes the caller's binding. Removing the last one
  starts the context's deletion: no new work is admitted, active tasks are
  cancelled, and once all of them are terminal the context's tasks, push
  configurations and bindings are removed in one step.

A ``ContextCatalog`` answers those questions from the store that holds the
context's tasks. The orchestration around it - cancelling tasks, deleting the
framework state, turning outcomes into errors - is ``aion.server.contexts``.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol

from a2a.types import Artifact, Message, Task, TaskStatus

from aion.core.a2a import A2AMetadataKey, ArtifactId, MessageType
from aion.server.tasks.admission import ContextHolder

__all__ = [
    "ARTIFACT_WINDOW",
    "EXCLUDED_ARTIFACT_IDS",
    "ContextActivity",
    "ContextCatalog",
    "ContextPage",
    "ContextTaskState",
    "DeletionStep",
    "DeletionTicket",
    "FinishOutcome",
    "conversation_of",
    "is_conversation_message",
]

ARTIFACT_WINDOW = 50
"""Most artifacts ``GetContext`` returns: the latest ones, newest first."""

EXCLUDED_ARTIFACT_IDS = frozenset({ArtifactId.STREAM_DELTA.value, ArtifactId.THINKING_DELTA.value})
"""Streaming artifacts that are progress, not results, and never appear in a context."""


def is_conversation_message(message: Message) -> bool:
    """Whether a stored message is part of the conversation rather than an internal event.

    Messages carrying an ``aion:messageType`` other than ``message`` -
    framework events, LangGraph values - are history the agent keeps for
    itself, not turns of the conversation.
    """
    if not message.HasField("metadata"):
        return True
    key = A2AMetadataKey.MESSAGE_TYPE.value
    if key not in message.metadata:
        return True
    return message.metadata[key] == MessageType.MESSAGE.value


def conversation_of(task: Task) -> list[Message]:
    """The conversation messages one task contributes to its context, in order.

    Its history plus the message still attached to its status - a task that
    ends on a status message has no later event to move it into history -
    filtered by :func:`is_conversation_message`.
    """
    messages = list(task.history)
    if task.status.HasField("message"):
        current = task.status.message
        if not messages or messages[-1] != current:
            messages.append(current)
    return [message for message in messages if is_conversation_message(message)]


@dataclass(frozen=True)
class ContextActivity:
    """A visible context and its latest activity."""

    context_id: str
    last_activity_at: datetime


@dataclass(frozen=True)
class ContextPage:
    """What ``GetContext`` returns for one visible context, before serialization.

    Attributes:
        history: The selected page of conversation messages, chronological.
        artifacts: ``(task_id, artifact)`` pairs, newest first, at most
            :data:`ARTIFACT_WINDOW`.
        status: Status of the most recently active task; an empty status
            when the context has no task yet.
        last_activity_at: Latest task activity, or the caller's binding
            time when the context has no task yet.
    """

    context_id: str
    history: list[Message]
    artifacts: list[tuple[str, Artifact]]
    status: TaskStatus
    last_activity_at: datetime


@dataclass(frozen=True)
class ContextTaskState:
    """One task of a context, whoever owns it, and its current state."""

    task_id: str
    state: int


class DeletionStep(enum.Enum):
    """Where ``begin_deletion`` left a context."""

    NOT_VISIBLE = "not_visible"
    """The caller has no binding and is not resuming a deletion: nothing to do."""

    UNBOUND = "unbound"
    """The caller's binding was removed; other callers are still bound."""

    STARTED = "started"
    """The caller's was the last binding; the context is now ``deleting``."""

    RESUMED = "resumed"
    """The context was already ``deleting`` at this caller's request."""


@dataclass(frozen=True)
class DeletionTicket:
    """The outcome of ``begin_deletion`` and what is needed to drive a deletion on."""

    step: DeletionStep
    operation_id: Optional[str] = None
    holder: Optional[ContextHolder] = None


class FinishOutcome(enum.Enum):
    """Where ``finish_deletion`` left a context."""

    FINISHED = "finished"
    """The context's tasks and bindings are gone and it is ``deleted``."""

    NOT_SETTLED = "not_settled"
    """A task is still active; nothing was removed."""

    SUPERSEDED = "superseded"
    """The deletion is no longer the one in progress: another request finished or abandoned it."""


class ContextCatalog(Protocol):
    """Context visibility, reads and the deletion lifecycle, over one task store."""

    async def list_contexts(self, member: str, *, limit: int, offset: int) -> list[ContextActivity]:
        """The contexts ``member`` is bound to, most recently active first."""

    async def read_context(
        self,
        context_id: str,
        member: str,
        *,
        history_length: int,
        history_offset: int,
    ) -> Optional[ContextPage]:
        """One context ``member`` is bound to; ``None`` for any other.

        History is selected newest-relative - ``history_offset`` newest
        conversation messages skipped, then up to ``history_length`` taken -
        and returned chronologically.
        """

    async def begin_deletion(self, context_id: str, member: str) -> DeletionTicket:
        """Remove ``member``'s binding, and start the deletion when it was the last one.

        A context already being deleted at ``member``'s request is resumed
        rather than started again, so a retry after a timeout or a restart
        continues the same operation.
        """

    async def context_tasks(self, context_id: str) -> list[ContextTaskState]:
        """Every task of the context, whoever owns it."""

    async def finish_deletion(self, context_id: str, operation_id: str) -> tuple[FinishOutcome, list[str]]:
        """Remove the context's tasks, their push configurations and its bindings, if all are terminal.

        Returns:
            The outcome, and the IDs of the tasks removed when it is
            :attr:`FinishOutcome.FINISHED`.
        """

    async def abandon_deletion(self, context_id: str, operation_id: str) -> None:
        """Return a context whose deletion was refused to ``active``; its removed bindings stay removed."""

    async def pending_deletions(self) -> list[tuple[str, DeletionTicket]]:
        """Every context still ``deleting``, with what is needed to drive its deletion on."""


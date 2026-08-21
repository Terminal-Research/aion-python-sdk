"""Pydantic models for database records."""

from __future__ import annotations

import datetime as _dt
import uuid
from pydantic import BaseModel, ConfigDict, model_validator

from a2a.types import Artifact, Message, Task, TaskStatus
from google.protobuf.struct_pb2 import Struct

__all__ = [
    "TaskRecord",
    "TaskClaimRecord",
    "TaskMessageRecord",
    "TaskArtifactRecord",
    "resolve_status_timestamp",
]


def resolve_status_timestamp(status: TaskStatus) -> tuple[TaskStatus, _dt.datetime]:
    """Return a status stamped with a timestamp, and that timestamp as a datetime.

    ``status_timestamp`` must always equal ``TaskStatus.timestamp``: this is
    the single place that relationship is computed, so every writer that needs
    the pair calls it instead of deriving one side and guessing the other.
    A status that already carries a timestamp is returned unchanged; one that
    does not is returned as a copy stamped with the current UTC time, so the
    JSON snapshot and the typed column always agree.
    """
    if status.HasField("timestamp"):
        return status, status.timestamp.ToDatetime(tzinfo=_dt.timezone.utc)
    stamped = TaskStatus()
    stamped.CopyFrom(status)
    now = _dt.datetime.now(_dt.timezone.utc)
    stamped.timestamp.FromDatetime(now)
    return stamped, now


class TaskRecord(BaseModel):
    """Pydantic representation of a row from the ``tasks`` table.

    Used as the public return type from :class:`TasksRepository` so that
    callers work with typed Pydantic objects rather than raw ORM models.

    This is the task's compact current-state head only: ``history`` and
    ``artifacts`` live in :class:`TaskMessageRecord`/:class:`TaskArtifactRecord`
    rows instead, fetched separately by whoever assembles the full A2A
    ``Task`` (see :meth:`to_task`).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: uuid.UUID
    """Auto-generated UUID primary key."""
    agent_id: str
    """Identity of the agent this task belongs to, scoping every query."""
    context_id: str
    """A2A context ID that groups related tasks together."""
    status: TaskStatus
    """Current A2A task status (protobuf TaskStatus)."""
    status_timestamp: _dt.datetime | None = None
    """``status.timestamp`` as a typed value; recomputed on construction and
    never diverges from it, regardless of which field the caller provided."""
    task_metadata: Struct | None = None
    """Arbitrary key-value metadata attached to the task (protobuf Struct)."""
    created_at: _dt.datetime | None = None
    """Timestamp when the record was first inserted into the database."""
    updated_at: _dt.datetime | None = None
    """Timestamp of the most recent update to this record."""

    @model_validator(mode="after")
    def _resolve_status_timestamp(self) -> TaskRecord:
        """Keep ``status_timestamp`` locked to ``status.timestamp``.

        Running on every construction path - direct instantiation,
        :meth:`from_task`, and loading a row back with
        ``model_validate(..., from_attributes=True)`` - means the two fields
        can never be set independently, which is what makes the invariant
        impossible to violate by omission.
        """
        status, status_timestamp = resolve_status_timestamp(self.status)
        self.status = status
        self.status_timestamp = status_timestamp
        return self

    @classmethod
    def from_task(cls, task: Task, agent_id: str) -> TaskRecord:
        """Build a database record from an A2A ``Task``.

        The counterpart of :meth:`to_task`. Kept here so that every writer
        serialises a task the same way, rather than each reaching into
        whichever caller happened to implement the conversion first.

        Args:
            task: The task to persist. ``task.id`` must be a UUID string.
            agent_id: Identity of the agent process persisting this task.
                A2A's ``Task`` carries no such identity itself, so the caller
                must name it explicitly.

        Returns:
            A record ready to hand to :class:`TasksRepository`.

        Raises:
            ValueError: If ``task.id`` is not a UUID string.
        """
        return cls(
            id=uuid.UUID(task.id),
            agent_id=agent_id,
            context_id=task.context_id,
            status=task.status,
            task_metadata=task.metadata if task.HasField("metadata") else None,
        )

    def to_task(
        self,
        task_id: str,
        *,
        history: list[Message] | None = None,
        artifacts: list[Artifact] | None = None,
    ) -> Task:
        """Reconstruct an A2A ``Task`` object from this head plus its children.

        Args:
            task_id: The string task identifier to assign (the DB ``id`` is a UUID,
                     while A2A uses a plain string identifier).
            history: This task's messages, in order, when the caller fetched
                them. Omitted entirely (not merely empty) for a caller that
                never needs them, such as the reaper reading only status.
            artifacts: This task's artifacts, when the caller fetched them.

        Returns:
            A populated :class:`a2a.types.Task` instance.
        """
        return Task(
            id=task_id,
            context_id=self.context_id,
            status=self.status,
            artifacts=artifacts,
            history=history,
            metadata=self.task_metadata,
        )


class TaskClaimRecord(BaseModel):
    """Pydantic representation of a row from the ``task_claims`` table.

    A plain snapshot of the database row. It carries no opinion about what a
    caller may infer from it — ``aion.server.tasks.ownership.Claim`` is the
    type that adds the fail-closed local deadline and the rest of the
    process-side meaning; this one only mirrors the columns.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    task_id: uuid.UUID
    """Identifier of the task whose execution lease this row holds."""
    agent_id: str
    """Identity of the agent process that owns this claim."""
    owner_token: uuid.UUID
    """Random incarnation token used for fencing writes."""
    lease_expires_at: _dt.datetime
    """Database timestamp after which another process may acquire the lease."""
    acquired_at: _dt.datetime | None = None
    """Timestamp at which this incarnation acquired the lease."""
    renewed_at: _dt.datetime | None = None
    """Timestamp of the most recent successful renewal."""
    owner_instance_id: str | None = None
    """Best-effort pod/process identity used for diagnostics only."""


class TaskMessageRecord(BaseModel):
    """Pydantic representation of a row from the ``task_messages`` table."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    task_id: uuid.UUID
    """Task this history entry belongs to."""
    seq: int
    """Position of this entry in ``Task.history``, zero-based."""
    message_id: str | None = None
    """A2A ``message_id``, when the message carried one."""
    payload: Message
    """The A2A ``Message``."""
    created_at: _dt.datetime | None = None
    """Timestamp this entry was written."""


class TaskArtifactRecord(BaseModel):
    """Pydantic representation of a row from the ``task_artifacts`` table."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    task_id: uuid.UUID
    """Task this artifact belongs to."""
    artifact_id: str
    """A2A ``artifact_id``, unique within the task."""
    payload: Artifact
    """The A2A ``Artifact``."""
    created_at: _dt.datetime | None = None
    """Timestamp this artifact was first written."""
    updated_at: _dt.datetime | None = None
    """Timestamp this artifact's payload was last replaced."""

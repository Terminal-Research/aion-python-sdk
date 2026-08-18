"""Pydantic models for database records."""

from __future__ import annotations

import datetime as _dt
import uuid
from pydantic import BaseModel, ConfigDict

from a2a.types import Artifact, Message, Task, TaskStatus
from google.protobuf.struct_pb2 import Struct

__all__ = [
    "TaskRecord",
    "TaskClaimRecord",
]


class TaskRecord(BaseModel):
    """Pydantic representation of a row from the ``tasks`` table.

    Used as the public return type from :class:`TasksRepository` so that
    callers work with typed Pydantic objects rather than raw ORM models.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: uuid.UUID
    """Auto-generated UUID primary key."""
    context_id: str
    """A2A context ID that groups related tasks together."""
    status: TaskStatus
    """Current A2A task status (protobuf TaskStatus)."""
    artifacts: list[Artifact] | None = None
    """Output artifacts produced by the task, if any."""
    history: list[Message] | None = None
    """Conversation message history associated with the task, if any."""
    task_metadata: Struct | None = None
    """Arbitrary key-value metadata attached to the task (protobuf Struct)."""
    created_at: _dt.datetime | None = None
    """Timestamp when the record was first inserted into the database."""
    updated_at: _dt.datetime | None = None
    """Timestamp of the most recent update to this record."""

    @classmethod
    def from_task(cls, task: Task) -> TaskRecord:
        """Build a database record from an A2A ``Task``.

        The counterpart of :meth:`to_task`. Kept here so that every writer
        serialises a task the same way, rather than each reaching into
        whichever caller happened to implement the conversion first.

        Args:
            task: The task to persist. ``task.id`` must be a UUID string.

        Returns:
            A record ready to hand to :class:`TasksRepository`.

        Raises:
            ValueError: If ``task.id`` is not a UUID string.
        """
        return cls(
            id=uuid.UUID(task.id),
            context_id=task.context_id,
            status=task.status,
            artifacts=task.artifacts,
            history=task.history,
            task_metadata=task.metadata if task.HasField("metadata") else None,
        )

    def to_task(self, task_id: str) -> Task:
        """Reconstruct an A2A ``Task`` object from this database record.

        Args:
            task_id: The string task identifier to assign (the DB ``id`` is a UUID,
                     while A2A uses a plain string identifier).

        Returns:
            A populated :class:`a2a.types.Task` instance.
        """
        return Task(
            id=task_id,
            context_id=self.context_id,
            status=self.status,
            artifacts=self.artifacts,
            history=self.history,
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

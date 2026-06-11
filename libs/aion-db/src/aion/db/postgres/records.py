"""Pydantic models for database records."""

from __future__ import annotations

import datetime as _dt
import uuid
from pydantic import BaseModel, ConfigDict

from a2a.types import Artifact, Message, Task, TaskStatus
from google.protobuf.struct_pb2 import Struct

__all__ = [
    "TaskRecord",
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
    created_at: _dt.datetime
    """Timestamp when the record was first inserted into the database."""
    updated_at: _dt.datetime
    """Timestamp of the most recent update to this record."""

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

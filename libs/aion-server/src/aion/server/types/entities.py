"""Pydantic models for database records."""

from __future__ import annotations

import datetime as _dt
import uuid
from typing import Any

from pydantic import BaseModel

try:  # pragma: no cover - optional dependency
    from a2a.types import Artifact, Message, Task, TaskStatus
except Exception as exc:  # pragma: no cover - explicit failure if missing
    raise ImportError(
        "The 'a2a-sdk' package is required to use these models"
    ) from exc

__all__ = [
    "TaskRecord",
]


class TaskRecord(BaseModel):
    """Database record model for storing task information."""

    id: uuid.UUID
    context_id: str
    status: TaskStatus
    artifacts: list[Artifact] | None = None
    history: list[Message] | None = None
    task_metadata: dict[str, Any] | None = None
    created_at: _dt.datetime
    updated_at: _dt.datetime

    def to_task(self, task_id: str) -> Task:
        """Reconstruct a Task from this record."""
        return Task(
            id=task_id,
            context_id=self.context_id,
            status=self.status,
            artifacts=self.artifacts,
            history=self.history,
            metadata=self.task_metadata,
        )

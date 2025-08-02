"""Pydantic models for database records."""

from __future__ import annotations

import datetime as _dt
import uuid

from pydantic import BaseModel

try:  # pragma: no cover - optional dependency
    from a2a.types import Task
except Exception as exc:  # pragma: no cover - explicit failure if missing
    raise ImportError(
        "The 'a2a-sdk' package is required to use these models"
    ) from exc

__all__ = [
    "TaskRecord",
]


class TaskRecord(BaseModel):
    """Database record model for storing task information.

    Represents a task entry in the database with metadata including
    creation and update timestamps, context association, and the task data itself.
    """
    id: uuid.UUID
    context_id: str
    task: Task
    created_at: _dt.datetime
    updated_at: _dt.datetime

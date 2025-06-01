"""Pydantic models for database records."""

from __future__ import annotations

import datetime as _dt
import uuid
from typing import List

from pydantic import BaseModel

try:  # pragma: no cover - optional dependency
    from a2a.types import Artifact, Task
except Exception as exc:  # pragma: no cover - explicit failure if missing
    raise ImportError(
        "The 'a2a-sdk' package is required to use these models"
    ) from exc


class ThreadRecord(BaseModel):
    """Representation of a row in the ``threads`` table."""

    id: uuid.UUID
    context_id: str
    artifacts: List[Artifact]
    created_at: _dt.datetime
    updated_at: _dt.datetime


class TaskRecord(BaseModel):
    """Representation of a row in the ``tasks`` table."""

    id: uuid.UUID
    context_id: str
    task: Task
    created_at: _dt.datetime
    updated_at: _dt.datetime

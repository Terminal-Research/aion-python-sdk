"""Pydantic models for database records."""

from __future__ import annotations

import uuid
from sqlalchemy import Column, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base

from .fields import JSONType


try:  # pragma: no cover - optional dependency
    from a2a.types import Artifact, Task
except Exception as exc:  # pragma: no cover - explicit failure if missing
    raise ImportError(
        "The 'a2a-sdk' package is required to use these models"
    ) from exc


__all__ = [
    "BaseModel",
    "TaskRecordModel",
]

BaseModel = declarative_base()

class TaskRecordModel(BaseModel):
    """Representation of a row in the ``tasks`` table."""

    __tablename__ = 'tasks'

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4)

    context_id = Column(
        String,
        nullable=False,
        index=True)

    task = Column(
        JSONType,
        nullable=False)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now())

    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now())

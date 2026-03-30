"""SQLAlchemy models for database records."""

from __future__ import annotations

import uuid
from sqlalchemy import Column, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base
from google.protobuf.struct_pb2 import Struct

from .constants import TASKS_TABLE
from .fields import ProtobufType


try:  # pragma: no cover - optional dependency
    from a2a.types import Artifact, Message, TaskStatus
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

    __tablename__ = TASKS_TABLE

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4)

    context_id = Column(
        String,
        nullable=False,
        index=True)

    status = Column(
        ProtobufType(TaskStatus),
        nullable=False)

    artifacts = Column(
        ProtobufType(Artifact, many=True),
        nullable=True)

    history = Column(
        ProtobufType(Message, many=True),
        nullable=True)

    task_metadata = Column(
        "metadata",
        ProtobufType(Struct),
        nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now())

    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now())

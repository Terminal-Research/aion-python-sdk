"""SQLAlchemy models for database records."""

from __future__ import annotations

import uuid
from sqlalchemy import Column, Computed, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base
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
        default=uuid.uuid4,
        doc="Auto-generated UUID primary key.")

    context_id = Column(
        String,
        nullable=False,
        index=True,
        doc="A2A context ID grouping related tasks together.")

    status = Column(
        ProtobufType(TaskStatus),
        nullable=False,
        doc="Current task status stored as JSONB (serialized TaskStatus protobuf).")

    state = Column(
        Text,
        Computed(
            "COALESCE(status->>'state', 'TASK_STATE_UNSPECIFIED')",
            persisted=True,
        ),
        nullable=False,
        doc="Task state projected from status for filtering.")

    status_timestamp = Column(
        DateTime(timezone=True),
        Computed(
            "aion.rfc3339_to_timestamptz(status->>'timestamp')",
            persisted=True,
        ),
        nullable=True,
        doc="Task status timestamp projected from status for filtering and sorting.")

    artifacts = Column(
        ProtobufType(Artifact, many=True),
        nullable=True,
        doc="List of task output artifacts stored as a JSONB array.")

    history = Column(
        ProtobufType(Message, many=True),
        nullable=True,
        doc="Conversation message history stored as a JSONB array.")

    task_metadata = Column(
        "metadata",
        ProtobufType(Struct),
        nullable=True,
        doc="Arbitrary key-value metadata stored as a JSONB object (Protobuf Struct).")

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        doc="Timestamp of record creation, set automatically by the database.")

    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        doc="Timestamp of last record update, refreshed automatically on every write.")

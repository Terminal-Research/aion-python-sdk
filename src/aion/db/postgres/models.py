"""SQLAlchemy models for database records."""

from __future__ import annotations

import uuid
from sqlalchemy import BigInteger, Column, Computed, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base
from google.protobuf.struct_pb2 import Struct

from .constants import TASK_ARTIFACTS_TABLE, TASK_CLAIMS_TABLE, TASK_MESSAGES_TABLE, TASKS_TABLE
from .fields import ProtobufType


try:  # pragma: no cover - optional dependency
    from a2a.types import Artifact, Message, TaskStatus
except Exception as exc:  # pragma: no cover - explicit failure if missing
    raise ImportError(
        "The 'a2a-sdk' package is required to use these models"
    ) from exc


__all__ = [
    "BaseModel",
    "TaskClaimModel",
    "TaskRecordModel",
    "TaskMessageModel",
    "TaskArtifactModel",
]

BaseModel = declarative_base()


class TaskClaimModel(BaseModel):
    """Representation of a task's expiring execution lease.

    The claim table deliberately has no foreign key to ``tasks``. A new task is
    claimed before its first durable row is written, and an expired orphan is
    safe to remove during reconciliation.
    """

    __tablename__ = TASK_CLAIMS_TABLE

    task_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        doc="UUID of the task whose execution lease is held.",
    )
    agent_id = Column(
        Text,
        nullable=False,
        doc="Identity of the agent process that owns this claim.",
    )
    owner_token = Column(
        UUID(as_uuid=True),
        nullable=False,
        doc="Random incarnation token used for fencing writes.",
    )
    lease_expires_at = Column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
        doc="Database timestamp after which another process may acquire the lease.",
    )
    acquired_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
        doc="Timestamp at which this incarnation acquired the lease.",
    )
    renewed_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
        doc="Timestamp of the most recent successful renewal.",
    )
    owner_instance_id = Column(
        Text,
        nullable=True,
        doc="Best-effort pod/process identity used for diagnostics only.",
    )
    cancel_requested_at = Column(
        DateTime(timezone=True),
        nullable=True,
        doc=(
            "When a non-owner asked this claim's owner to cancel the task. "
            "NULL means no cancellation is pending. Rides on the claim: it "
            "disappears with the incarnation it was addressed to."
        ),
    )


class TaskRecordModel(BaseModel):
    """Representation of a row in the ``tasks`` table."""

    __tablename__ = TASKS_TABLE

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        doc="Auto-generated UUID primary key.")

    agent_id = Column(
        Text,
        nullable=False,
        index=True,
        doc="Identity of the agent this task belongs to, scoping every query.")

    owner_scope = Column(
        Text,
        nullable=False,
        doc="Stable effective-caller scope that owns this task's context.")

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
        nullable=False,
        doc="Task status timestamp, written by the application from status.timestamp.")

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


class TaskMessageModel(BaseModel):
    """One entry of a task's durable history.

    ``seq`` is the entry's position in ``Task.history`` at the time it was
    written — append-only, since that is how the A2A event pipeline builds
    history in memory. ``(task_id, seq)`` is the primary key, which is what
    makes writing the same entry twice a no-op rather than a duplicate.
    """

    __tablename__ = TASK_MESSAGES_TABLE

    task_id = Column(
        UUID(as_uuid=True),
        ForeignKey(f"{TASKS_TABLE}.id", ondelete="CASCADE"),
        primary_key=True,
        doc="Task this history entry belongs to.")

    seq = Column(
        BigInteger(),
        primary_key=True,
        doc="Position of this entry in Task.history, zero-based.")

    message_id = Column(
        Text,
        nullable=True,
        doc="A2A message_id, when the message carried one; used for redelivery idempotency.")

    payload = Column(
        ProtobufType(Message),
        nullable=False,
        doc="The A2A Message, stored as JSONB.")

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
        doc="Timestamp this entry was written.")


class TaskArtifactModel(BaseModel):
    """One artifact of a task, keyed by its own A2A identity.

    A later chunk of the same artifact overwrites this row rather than
    appending a new one: the A2A event pipeline already merges an artifact's
    parts in memory (``append_artifact_to_task``) before a save is ever made,
    so what reaches here is always the artifact's full current content.
    """

    __tablename__ = TASK_ARTIFACTS_TABLE

    task_id = Column(
        UUID(as_uuid=True),
        ForeignKey(f"{TASKS_TABLE}.id", ondelete="CASCADE"),
        primary_key=True,
        doc="Task this artifact belongs to.")

    artifact_id = Column(
        Text,
        primary_key=True,
        doc="A2A artifact_id, unique within the task.")

    payload = Column(
        ProtobufType(Artifact),
        nullable=False,
        doc="The A2A Artifact, stored as JSONB.")

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
        doc="Timestamp this artifact was first written.")

    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
        onupdate=func.clock_timestamp(),
        doc="Timestamp this artifact's payload was last replaced.")

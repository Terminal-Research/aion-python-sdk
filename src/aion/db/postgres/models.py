"""SQLAlchemy models for database records."""

from __future__ import annotations

import uuid
from sqlalchemy import (
    BigInteger,
    Column,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base
from google.protobuf.struct_pb2 import Struct
from a2a.types import Artifact, Message, TaskStatus

from .constants import (
    CONTEXT_BINDINGS_TABLE,
    CONTEXT_RESERVATIONS_TABLE,
    TASK_ARTIFACTS_TABLE,
    TASK_MESSAGES_TABLE,
    TASKS_TABLE,
)
from .fields import ProtobufType

__all__ = [
    "BaseModel",
    "ContextBindingModel",
    "ContextReservationModel",
    "TaskRecordModel",
    "TaskMessageModel",
    "TaskArtifactModel",
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


class ContextReservationModel(BaseModel):
    """Who may use a context of an agent, and where it is in its lifecycle: one row per ``(agent_id, context_id)``.

    The first request admitted into a context writes the row; every later
    request is admitted only if it is the same holder. A ``private`` context
    belongs to one caller, by ``owner_scope``; a ``shared`` one to an Aion
    gateway conversation, by the receiving agent identity and the edge
    environment of the invocation. A ``blocked`` context already had data when
    reservations were introduced and is admitted into by nobody.

    The row outlives the context's tasks, because the framework state it
    guards does. It changes in exactly two ways: ``DeleteContext`` moves it
    through ``deleting`` to ``deleted``, and the next request into a
    ``deleted`` context reserves it afresh for that request's holder.
    """

    __tablename__ = CONTEXT_RESERVATIONS_TABLE
    __table_args__ = (PrimaryKeyConstraint("agent_id", "context_id"),)

    agent_id = Column(Text, nullable=False, doc="Identity of the agent whose context this is.")
    context_id = Column(Text, nullable=False, doc="The A2A context ID.")
    kind = Column(Text, nullable=False, doc="``private``, ``shared`` or ``blocked``.")
    owner_scope = Column(Text, nullable=True, doc="The private context's owner; NULL for a shared one.")
    owner_agent_identity_id = Column(
        Text, nullable=True, doc="The shared context's receiving agent identity; NULL for a private one."
    )
    edge_agent_environment_id = Column(
        Text, nullable=True, doc="The shared context's edge environment; NULL for a private one."
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
        doc="When the first request was admitted into the context.",
    )
    state = Column(
        Text,
        nullable=False,
        server_default="active",
        doc="``active``, ``deleting`` or ``deleted``.",
    )
    deletion_operation_id = Column(
        Text, nullable=True, doc="The durable deletion in progress or last finished."
    )
    deletion_requested_by = Column(
        Text, nullable=True, doc="``owner_scope`` of the caller whose request started the deletion."
    )
    deleted_at = Column(DateTime(timezone=True), nullable=True, doc="When the deletion finished.")


class ContextBindingModel(BaseModel):
    """A caller bound to a context: one row per ``(agent_id, context_id, owner_scope)``.

    Written when a caller is admitted into the context, removed by that
    caller's ``DeleteContext``. A caller sees a context through the Context
    extension exactly while it has a binding to it.
    """

    __tablename__ = CONTEXT_BINDINGS_TABLE
    __table_args__ = (
        PrimaryKeyConstraint("agent_id", "context_id", "owner_scope"),
        ForeignKeyConstraint(
            ["agent_id", "context_id"],
            [f"{CONTEXT_RESERVATIONS_TABLE}.agent_id", f"{CONTEXT_RESERVATIONS_TABLE}.context_id"],
            ondelete="CASCADE",
        ),
    )

    agent_id = Column(Text, nullable=False, doc="Identity of the agent whose context this is.")
    context_id = Column(Text, nullable=False, doc="The A2A context ID.")
    owner_scope = Column(Text, nullable=False, doc="The bound caller's owner scope.")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
        doc="When the caller was first admitted into the context.",
    )

"""Add a2a-sdk's cluster tables and drop task claims.

``task_versions`` and ``task_events`` are the two tables a2a-sdk's cluster
mode reads and writes: one version row per task, advanced by a
compare-and-swap on every write, and an append-only journal of the events that
produced each version. Their columns are a2a-sdk's own
(``TaskVersionMixin`` and ``TaskEventMixin`` in ``a2a.server.models``, revision
``b5e3d1c8a2f7`` of ``a2a/migrations``), so a2a-sdk's
``DatabaseTaskEventStream`` reads the journal as it is written here. The
``tasks`` table and its message and artifact tables are unchanged; a task with
no version row is read as ``TaskVersion.MISSING`` and gets one on its next
write.

``task_claims`` held the execution lease of the ownership scheme the cluster
tables replace. Nothing reads it any more, so it is dropped. The downgrade
recreates it empty: a lease is not data worth keeping across a downgrade.

The table names are written out here rather than imported: this revision has
to describe the schema as it was when it ran.

Revision ID: 008
Revises: 007
"""

from alembic import op
import sqlalchemy as sa


revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None

_VERSIONS = "task_versions"
_EVENTS = "task_events"
_CLAIMS = "task_claims"


def upgrade() -> None:
    """Create the version and journal tables, then drop the claims table."""
    op.create_table(
        _VERSIONS,
        sa.Column("task_id", sa.String(36), primary_key=True),
        sa.Column("owner", sa.String(255), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        _EVENTS,
        sa.Column("seq", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("owner", sa.String(255), nullable=True),
        sa.Column("task_version", sa.BigInteger(), nullable=False),
        sa.Column("event_data", sa.LargeBinary(), nullable=False),
    )
    op.create_index(f"ix_{_EVENTS}_task_id", _EVENTS, ["task_id"])

    op.drop_index("task_claims_cancel_requested_idx", table_name=_CLAIMS)
    op.drop_index("task_claims_expiry_idx", table_name=_CLAIMS)
    op.drop_table(_CLAIMS)


def downgrade() -> None:
    """Recreate an empty claims table and drop the version and journal tables."""
    op.create_table(
        _CLAIMS,
        sa.Column("task_id", sa.Uuid(), primary_key=True),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("owner_token", sa.Uuid(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "acquired_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column(
            "renewed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("owner_instance_id", sa.Text(), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("task_claims_expiry_idx", _CLAIMS, ["lease_expires_at"])
    op.create_index(
        "task_claims_cancel_requested_idx",
        _CLAIMS,
        ["cancel_requested_at"],
        postgresql_where=sa.text("cancel_requested_at IS NOT NULL"),
    )

    op.drop_index(f"ix_{_EVENTS}_task_id", table_name=_EVENTS)
    op.drop_table(_EVENTS)
    op.drop_table(_VERSIONS)

"""Reserve each context for the one holder admitted into it first.

A context is private to one caller (``owner_scope``) or shared by one Aion
gateway conversation (the receiving agent identity and the edge environment).
Task rows cannot say which: a context holds tasks of several initiators, and
they come and go while the framework state keyed by the context stays. The
reservation is written by the first request admitted into the context, before
any task or state exists, and is never updated or deleted.

Revision ID: 006
Revises: 005
"""

from alembic import op
import sqlalchemy as sa

from aion.db.postgres.constants import CONTEXT_RESERVATIONS_TABLE


revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the context reservations table."""
    op.create_table(
        CONTEXT_RESERVATIONS_TABLE,
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("context_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("owner_scope", sa.Text(), nullable=True),
        sa.Column("owner_agent_identity_id", sa.Text(), nullable=True),
        sa.Column("edge_agent_environment_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.PrimaryKeyConstraint("agent_id", "context_id"),
        sa.CheckConstraint(
            "(kind = 'private' AND owner_scope IS NOT NULL AND owner_scope <> ''"
            " AND owner_agent_identity_id IS NULL AND edge_agent_environment_id IS NULL)"
            " OR (kind = 'shared' AND owner_scope IS NULL"
            " AND owner_agent_identity_id IS NOT NULL AND edge_agent_environment_id IS NOT NULL)",
            name="context_reservations_holder_shape",
        ),
    )


def downgrade() -> None:
    """Drop the context reservations table."""
    op.drop_table(CONTEXT_RESERVATIONS_TABLE)

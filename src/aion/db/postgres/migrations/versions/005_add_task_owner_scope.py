"""Persist the effective caller that owns each task context.

Revision ID: 005
Revises: 004
"""

from alembic import op
import sqlalchemy as sa

from aion.db.postgres.constants import TASKS_TABLE


revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None

OWNER_CONTEXT_INDEX = "ix_tasks_agent_owner_context_created_at"


def upgrade() -> None:
    """Add the durable task owner and its context-directory index."""
    op.add_column(
        TASKS_TABLE,
        sa.Column(
            "owner_scope",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
        ),
    )
    op.alter_column(TASKS_TABLE, "owner_scope", server_default=None)
    op.create_index(
        OWNER_CONTEXT_INDEX,
        TASKS_TABLE,
        ["agent_id", "owner_scope", "context_id", "created_at"],
    )


def downgrade() -> None:
    """Remove durable task context ownership."""
    op.drop_index(OWNER_CONTEXT_INDEX, table_name=TASKS_TABLE)
    op.drop_column(TASKS_TABLE, "owner_scope")

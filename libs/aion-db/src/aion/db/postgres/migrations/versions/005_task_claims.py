"""Add expiring task ownership claims used for multi-instance execution."""

import logging

from alembic import op
import sqlalchemy as sa

from aion.db.postgres.constants import TASK_CLAIMS_TABLE


revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)


def upgrade() -> None:
    """Create the task claim table and its expiry index.

    There is intentionally no foreign key to ``tasks``: a claim is acquired
    before the first task row exists, and expired orphan claims are harmless
    reconciliation candidates.
    """
    logger.debug("Creating task ownership claims table")
    op.create_table(
        TASK_CLAIMS_TABLE,
        sa.Column("task_id", sa.Uuid(), primary_key=True),
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
    )
    op.create_index(
        "task_claims_expiry_idx",
        TASK_CLAIMS_TABLE,
        ["lease_expires_at"],
    )


def downgrade() -> None:
    """Drop the task claim table and expiry index."""
    logger.debug("Dropping task ownership claims table")
    op.drop_index("task_claims_expiry_idx", table_name=TASK_CLAIMS_TABLE)
    op.drop_table(TASK_CLAIMS_TABLE)

"""Create threads table."""

from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None

from aion.shared.logging import get_logger
logger = get_logger(__name__)


def upgrade() -> None:
    """Create threads table."""
    logger.debug("Creating threads table")
    op.create_table(
        "threads",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("context_id", sa.String(), unique=True, nullable=False),
        sa.Column("artifacts", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    """Drop threads table."""
    op.drop_table("threads")

"""Create tasks table."""
from aion.shared.logging import get_logger
from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None

from aion.shared.logging import get_logger
logger = get_logger(__name__)

def upgrade() -> None:
    """Create tasks table."""
    logger.debug("Creating tasks table")
    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("context_id", sa.String(), unique=True, nullable=False),
        sa.Column("task", sa.JSON(), nullable=False),
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
    op.create_index("ix_tasks_context_id", "tasks", ["context_id"])


def downgrade() -> None:
    """Drop tasks table."""
    op.drop_table("tasks")

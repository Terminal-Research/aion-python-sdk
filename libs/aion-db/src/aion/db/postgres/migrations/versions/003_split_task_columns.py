"""Split task JSON column into status, artifacts, history, metadata; add context_id index."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None

from aion.shared.logging import get_logger
logger = get_logger(__name__)


def upgrade() -> None:
    """Replace the monolithic task JSON column with dedicated columns and add context_id index."""
    logger.debug("Splitting task column into status/artifacts/history/metadata")

    op.drop_column("tasks", "task")

    op.add_column("tasks", sa.Column("status", JSONB(), nullable=False, server_default="{}"))
    op.add_column("tasks", sa.Column("artifacts", JSONB(), nullable=True))
    op.add_column("tasks", sa.Column("history", JSONB(), nullable=True))
    op.add_column("tasks", sa.Column("metadata", JSONB(), nullable=True))

    op.alter_column("tasks", "status", server_default=None)

    logger.debug("Creating index on tasks(context_id, created_at)")
    op.create_index(
        "ix_tasks_context_id_created_at",
        "tasks",
        ["context_id", "created_at"],
    )

    logger.debug("Creating GIN index on tasks(artifacts)")
    op.create_index(
        "ix_tasks_artifacts_gin",
        "tasks",
        ["artifacts"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    """Restore the monolithic task JSON column and drop context_id index."""
    logger.debug("Dropping index ix_tasks_artifacts_gin")
    op.drop_index("ix_tasks_artifacts_gin", table_name="tasks")

    logger.debug("Dropping index ix_tasks_context_id_created_at")
    op.drop_index("ix_tasks_context_id_created_at", table_name="tasks")

    logger.debug("Restoring task column from status/artifacts/history/metadata")

    op.drop_column("tasks", "metadata")
    op.drop_column("tasks", "history")
    op.drop_column("tasks", "artifacts")
    op.drop_column("tasks", "status")

    op.add_column("tasks", sa.Column("task", JSONB(), nullable=False, server_default="{}"))
    op.alter_column("tasks", "task", server_default=None)

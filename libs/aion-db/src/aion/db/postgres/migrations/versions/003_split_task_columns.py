"""Split task JSON column into status, artifacts, history, metadata; add context_id index."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None

from aion.shared.logging import get_logger
from aion.db.postgres.constants import TASKS_TABLE
logger = get_logger(__name__)


def upgrade() -> None:
    """Replace the monolithic task JSON column with dedicated columns and add context_id index."""
    logger.debug("Splitting task column into status/artifacts/history/metadata")

    op.drop_column(TASKS_TABLE, "task")

    op.add_column(TASKS_TABLE, sa.Column("status", JSONB(), nullable=False, server_default="{}"))
    op.add_column(TASKS_TABLE, sa.Column("artifacts", JSONB(), nullable=True))
    op.add_column(TASKS_TABLE, sa.Column("history", JSONB(), nullable=True))
    op.add_column(TASKS_TABLE, sa.Column("metadata", JSONB(), nullable=True))

    op.alter_column(TASKS_TABLE, "status", server_default=None)

    logger.debug("Creating index on tasks(context_id, created_at)")
    op.create_index(
        "ix_tasks_context_id_created_at",
        TASKS_TABLE,
        ["context_id", "created_at"],
    )

    logger.debug("Creating GIN index on tasks(artifacts)")
    op.create_index(
        "ix_tasks_artifacts_gin",
        TASKS_TABLE,
        ["artifacts"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    """Restore the monolithic task JSON column and drop context_id index."""
    logger.debug("Dropping index ix_tasks_artifacts_gin")
    op.drop_index("ix_tasks_artifacts_gin", table_name=TASKS_TABLE)

    logger.debug("Dropping index ix_tasks_context_id_created_at")
    op.drop_index("ix_tasks_context_id_created_at", table_name=TASKS_TABLE)

    logger.debug("Restoring task column from status/artifacts/history/metadata")

    op.drop_column(TASKS_TABLE, "metadata")
    op.drop_column(TASKS_TABLE, "history")
    op.drop_column(TASKS_TABLE, "artifacts")
    op.drop_column(TASKS_TABLE, "status")

    op.add_column(TASKS_TABLE, sa.Column("task", JSONB(), nullable=False, server_default="{}"))
    op.alter_column(TASKS_TABLE, "task", server_default=None)

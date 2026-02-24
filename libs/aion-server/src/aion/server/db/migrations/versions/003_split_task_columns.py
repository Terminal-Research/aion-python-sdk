"""Split task JSON column into status, artifacts, history, metadata."""
from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None

from aion.shared.logging import get_logger
logger = get_logger(__name__)


def upgrade() -> None:
    """Replace the monolithic task JSON column with dedicated columns."""
    logger.debug("Splitting task column into status/artifacts/history/metadata")

    op.drop_column("tasks", "task")

    op.add_column("tasks", sa.Column("status", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("tasks", sa.Column("artifacts", sa.JSON(), nullable=True))
    op.add_column("tasks", sa.Column("history", sa.JSON(), nullable=True))
    op.add_column("tasks", sa.Column("metadata", sa.JSON(), nullable=True))

    op.alter_column("tasks", "status", server_default=None)


def downgrade() -> None:
    """Restore the monolithic task JSON column."""
    logger.debug("Restoring task column from status/artifacts/history/metadata")

    op.drop_column("tasks", "metadata")
    op.drop_column("tasks", "history")
    op.drop_column("tasks", "artifacts")
    op.drop_column("tasks", "status")

    op.add_column("tasks", sa.Column("task", sa.JSON(), nullable=False, server_default="{}"))
    op.alter_column("tasks", "task", server_default=None)

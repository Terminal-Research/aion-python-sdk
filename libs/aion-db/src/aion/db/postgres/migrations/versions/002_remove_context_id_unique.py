"""Remove unique constraint from context_id in tasks table."""
from alembic import op
from aion.db.postgres.constants import TASKS_TABLE

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None

from aion.shared.logging import get_logger
logger = get_logger(__name__)


def upgrade() -> None:
    """Remove unique constraint from context_id."""
    logger.debug("Removing unique constraint from context_id in tasks table")

    # Drop the unique constraint
    op.drop_constraint("tasks_context_id_key", TASKS_TABLE, type_="unique")


def downgrade() -> None:
    """Add back unique constraint to context_id."""
    logger.debug("Adding back unique constraint to context_id in tasks table")

    # Add back the unique constraint
    op.create_unique_constraint("tasks_context_id_key", TASKS_TABLE, ["context_id"])

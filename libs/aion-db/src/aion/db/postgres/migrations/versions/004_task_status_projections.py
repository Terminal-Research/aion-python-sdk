"""Project task status fields into typed generated columns."""

import logging

from alembic import op

from aion.db.postgres.constants import TASKS_TABLE


revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)


def upgrade() -> None:
    """Add generated state and timestamptz projections to ``tasks``."""
    logger.debug("Adding generated task state and status timestamp columns")

    op.execute(
        """
        CREATE FUNCTION aion.rfc3339_to_timestamptz(text)
        RETURNS timestamptz
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        AS $$
        BEGIN
            RETURN $1::timestamptz;
        EXCEPTION WHEN others THEN
            RETURN NULL;
        END
        $$;
        """
    )
    op.execute(
        f"""
        ALTER TABLE {TASKS_TABLE}
            ADD COLUMN state text
                GENERATED ALWAYS AS (
                    COALESCE(status->>'state', 'TASK_STATE_UNSPECIFIED')
                ) STORED NOT NULL,
            ADD COLUMN status_timestamp timestamptz
                GENERATED ALWAYS AS (
                    aion.rfc3339_to_timestamptz(status->>'timestamp')
                ) STORED
        """
    )


def downgrade() -> None:
    """Remove generated task status projections and their parser function."""
    logger.debug("Dropping generated task state and status timestamp columns")

    op.execute(
        f"""
        ALTER TABLE {TASKS_TABLE}
            DROP COLUMN status_timestamp,
            DROP COLUMN state
        """
    )
    op.execute("DROP FUNCTION aion.rfc3339_to_timestamptz(text)")

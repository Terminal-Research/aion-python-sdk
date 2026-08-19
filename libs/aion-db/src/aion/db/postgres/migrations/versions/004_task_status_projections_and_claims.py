"""Project task status fields into typed generated columns, add task claims, and index list order.

Merged from three migrations that were never released to ``main`` (originally
"004_task_status_projections", "005_task_claims", "006_task_list_order_index"):
no deployed database has applied them under their old revision ids, so folding
them into one still-unreleased migration carries no upgrade-path risk.
"""

import logging

from alembic import op
import sqlalchemy as sa

from aion.db.postgres.constants import TASK_CLAIMS_TABLE, TASKS_TABLE


revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)

TASKS_LIST_ORDER_INDEX = "tasks_status_timestamp_id_idx"


def upgrade() -> None:
    """Add generated status columns to ``tasks``, the task claims table, and their indexes."""
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

    logger.debug("Creating tasks list-order index")
    op.execute(
        f"""
        CREATE INDEX {TASKS_LIST_ORDER_INDEX}
            ON {TASKS_TABLE} (status_timestamp DESC NULLS LAST, id DESC)
        """
    )

    logger.debug("Creating task ownership claims table")
    # There is intentionally no foreign key to ``tasks``: a claim is acquired
    # before the first task row exists, and expired orphan claims are
    # harmless reconciliation candidates.
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
    """Drop the task claims table, the list-order index, and the status projections."""
    logger.debug("Dropping task ownership claims table")
    op.drop_index("task_claims_expiry_idx", table_name=TASK_CLAIMS_TABLE)
    op.drop_table(TASK_CLAIMS_TABLE)

    logger.debug("Dropping tasks list-order index")
    op.drop_index(TASKS_LIST_ORDER_INDEX, table_name=TASKS_TABLE)

    logger.debug("Dropping generated task state and status timestamp columns")
    op.execute(
        f"""
        ALTER TABLE {TASKS_TABLE}
            DROP COLUMN status_timestamp,
            DROP COLUMN state
        """
    )
    op.execute("DROP FUNCTION aion.rfc3339_to_timestamptz(text)")

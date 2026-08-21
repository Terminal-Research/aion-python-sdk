"""Project task status, add task claims, index list/state order, normalize history/artifacts.

Merged from six migrations that were never released to ``main`` (originally
"004_task_status_projections", "005_task_claims", "006_task_list_order_index",
"005_task_state_index", "006_status_timestamp",
"007_normalize_history_and_artifacts"): no deployed database has applied any
of them under their old revision ids, so folding them into one still-unreleased
migration carries no upgrade-path risk.

``status_timestamp`` is written by the application, not generated in SQL: an
earlier version of this migration computed it with a ``GENERATED ALWAYS AS``
column backed by a ``aion.rfc3339_to_timestamptz()`` function declared
``IMMUTABLE`` despite ``text::timestamptz`` actually being ``STABLE``. Since
nothing depended on that shape yet, the column is typed and filled directly
instead of carrying the mislabeled function forward.

``tasks.history`` and ``tasks.artifacts`` (added in ``003``) grow without a
bound: every message and every artifact chunk a task ever sees was appended to
one JSONB array, so one status update rewrote the whole array and its TOAST
storage, and the GIN index over ``artifacts`` reindexed on every one of those
rewrites too. This migration replaces both with ``task_messages`` (one row per
history entry, identified by ``(task_id, seq)``) and ``task_artifacts`` (one
row per artifact, identified by ``(task_id, artifact_id)`` - a later chunk
upserts the same row rather than appending a new one, matching how the A2A
event pipeline already merges an artifact's parts in memory before a save is
ever made). Both child tables cascade-delete with their task.

``tasks.agent_id`` and ``task_claims.agent_id`` identify which agent process
owns a row when one database is shared by several agents. Existing ``tasks``
rows predate the column and carry no such identity, so they are backfilled
with the ``UNKNOWN_AGENT_ID`` sentinel rather than left nullable: every reader
scopes its queries by ``agent_id`` unconditionally, and a nullable column
would force each of them to special-case ``NULL`` instead. ``task_claims`` has
no such rows yet (the table is created fresh in this same migration), so its
column is ``NOT NULL`` with no default - callers must always supply it.

Three index changes on ``tasks`` fix problems ``agent_id`` scoping exposed.
``STATE_INDEX`` leads with ``agent_id``, which every reader honors except the
claim reaper's orphan sweep - deliberately agent-agnostic, since one lease can
outlive any single agent's view of its own tasks. That sweep had no usable
index at all until ``ACTIVE_STATE_INDEX`` was added without ``agent_id``
leading it, and fell back to a sequential scan of the whole table on every
reconcile pass. ``AGENT_CONTEXT_INDEX`` carries a trailing ``created_at`` for
the same reason: ``find_unique_context_ids`` groups by ``context_id`` under
one ``agent_id`` and orders by ``max(created_at)``, and without that column
in the index every one of an agent's matching rows cost a heap fetch just to
read it - measured at ~130ms for one agent's ~80k tasks in a 16M-row table,
~40ms once the index covered the query outright. Separately,
``ix_tasks_context_id`` (``001``) is a plain prefix of
``ix_tasks_context_id_created_at`` (``003``): every lookup the narrower index
could serve, the wider one already does, so it is dropped here as dead weight
rather than carried forward.
"""

import logging

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from aion.db.postgres.constants import (
    TASK_ARTIFACTS_TABLE,
    TASK_CLAIMS_TABLE,
    TASK_MESSAGES_TABLE,
    TASKS_TABLE,
)


revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)

LIST_ORDER_INDEX = "tasks_status_timestamp_id_idx"
STATE_INDEX = "tasks_state_updated_id_idx"
ACTIVE_STATE_INDEX = "tasks_active_state_updated_id_idx"
ARTIFACTS_GIN_INDEX = "ix_tasks_artifacts_gin"
MESSAGE_ID_INDEX = "task_messages_message_id_idx"
AGENT_CONTEXT_INDEX = "ix_tasks_agent_id_context_id"
CONTEXT_ID_INDEX = "ix_tasks_context_id"
"""From ``001``. A plain prefix of ``ix_tasks_context_id_created_at`` (``003``),
so every lookup it could serve, that wider index already serves - dropped here
as dead weight rather than carried forward."""

UNKNOWN_AGENT_ID = "__unknown__"
"""Sentinel backfilled onto ``tasks`` rows written before ``agent_id`` existed."""


def upgrade() -> None:
    """Add task status projections, the claims table, their indexes, and normalize history/artifacts."""
    logger.debug("Adding generated task state column")
    op.execute(
        f"""
        ALTER TABLE {TASKS_TABLE}
            ADD COLUMN state text
                GENERATED ALWAYS AS (
                    COALESCE(status->>'state', 'TASK_STATE_UNSPECIFIED')
                ) STORED NOT NULL
        """
    )

    logger.debug("Adding typed status_timestamp column")
    op.add_column(
        TASKS_TABLE,
        sa.Column("status_timestamp", sa.DateTime(timezone=True), nullable=True),
    )

    # A row saved before this migration may already carry a protocol
    # timestamp in its JSON status. Backfill from it directly; a row with
    # none gets created_at instead, written into the JSON too, so the typed
    # column and the snapshot agree from the moment both exist.
    logger.debug("Backfilling status_timestamp from existing status JSON")
    op.execute(
        f"""
        UPDATE {TASKS_TABLE}
           SET status_timestamp = (status->>'timestamp')::timestamptz
         WHERE status ? 'timestamp'
        """
    )
    op.execute(
        f"""
        UPDATE {TASKS_TABLE}
           SET status = jsonb_set(
                   status,
                   '{{timestamp}}',
                   to_jsonb(to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS."000Z"'))
               ),
               status_timestamp = created_at
         WHERE status_timestamp IS NULL
        """
    )
    op.alter_column(TASKS_TABLE, "status_timestamp", nullable=False)

    logger.debug("Adding agent_id column to tasks, backfilled with the unknown sentinel")
    op.add_column(
        TASKS_TABLE,
        sa.Column(
            "agent_id",
            sa.Text(),
            nullable=False,
            server_default=sa.text(f"'{UNKNOWN_AGENT_ID}'"),
        ),
    )
    # The default only exists to backfill rows written before this column did;
    # every write from here on must name its own agent_id explicitly.
    op.alter_column(TASKS_TABLE, "agent_id", server_default=None)

    logger.debug("Creating tasks list-order and state indexes")
    op.execute(
        f"""
        CREATE INDEX {LIST_ORDER_INDEX}
            ON {TASKS_TABLE} (agent_id, status_timestamp DESC, id DESC)
        """
    )
    op.execute(
        f"""
        CREATE INDEX {STATE_INDEX}
            ON {TASKS_TABLE} (agent_id, state, updated_at, id)
        """
    )
    # Deliberately not led by agent_id: the reaper's orphan sweep is the only
    # reader of this shape, and it scans every agent's tasks by design (see
    # ClaimReaper._unowned_active_task_candidates). Without this index that
    # query has no usable one - STATE_INDEX above is unusable without an
    # agent_id predicate - and falls back to a sequential scan of the whole
    # table on every reconcile pass, growing with total tasks stored rather
    # than with the orphans actually found.
    op.execute(
        f"""
        CREATE INDEX {ACTIVE_STATE_INDEX}
            ON {TASKS_TABLE} (state, updated_at, id)
        """
    )
    # created_at is trailing rather than in AGENT_CONTEXT_INDEX's own equality
    # columns - find_unique_context_ids groups by context_id under agent_id
    # and orders by max(created_at), which this column serves without ever
    # widening the equality predicate. Included rather than left off: it
    # never changes after insert, so carrying it costs nothing on update, only
    # a larger index at insert time, in exchange for an Index Only Scan
    # instead of a heap fetch per matching row.
    logger.debug("Creating tasks agent/context lookup index")
    op.create_index(
        AGENT_CONTEXT_INDEX,
        TASKS_TABLE,
        ["agent_id", "context_id", "created_at"],
    )

    logger.debug("Dropping ix_tasks_context_id, superseded by its own prefix in 003")
    op.drop_index(CONTEXT_ID_INDEX, table_name=TASKS_TABLE)

    logger.debug("Creating task ownership claims table")
    # There is intentionally no foreign key to ``tasks``: a claim is acquired
    # before the first task row exists, and expired orphan claims are
    # harmless reconciliation candidates.
    op.create_table(
        TASK_CLAIMS_TABLE,
        sa.Column("task_id", sa.Uuid(), primary_key=True),
        sa.Column("agent_id", sa.Text(), nullable=False),
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

    logger.debug("Creating task_messages")
    op.create_table(
        TASK_MESSAGES_TABLE,
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey(f"{TASKS_TABLE}.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.Text(), nullable=True),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.PrimaryKeyConstraint("task_id", "seq"),
    )
    logger.debug("Indexing task_messages(message_id) for redelivery idempotency")
    op.execute(
        f"""
        CREATE UNIQUE INDEX {MESSAGE_ID_INDEX}
            ON {TASK_MESSAGES_TABLE} (task_id, message_id)
            WHERE message_id IS NOT NULL
        """
    )

    logger.debug("Creating task_artifacts")
    op.create_table(
        TASK_ARTIFACTS_TABLE,
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey(f"{TASKS_TABLE}.id", ondelete="CASCADE"), nullable=False),
        sa.Column("artifact_id", sa.Text(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.PrimaryKeyConstraint("task_id", "artifact_id"),
    )

    logger.debug("Backfilling task_messages and task_artifacts from existing snapshots")
    op.execute(
        f"""
        INSERT INTO {TASK_MESSAGES_TABLE} (task_id, seq, message_id, payload)
        SELECT t.id,
               ordinality - 1,
               message ->> 'messageId',
               message
          FROM {TASKS_TABLE} t,
               LATERAL jsonb_array_elements(COALESCE(t.history, '[]'::jsonb))
                   WITH ORDINALITY AS h(message, ordinality)
        """
    )
    op.execute(
        f"""
        INSERT INTO {TASK_ARTIFACTS_TABLE} (task_id, artifact_id, payload)
        SELECT t.id,
               artifact ->> 'artifactId',
               artifact
          FROM {TASKS_TABLE} t,
               LATERAL jsonb_array_elements(COALESCE(t.artifacts, '[]'::jsonb))
                   AS a(artifact)
         WHERE artifact ->> 'artifactId' IS NOT NULL
        """
    )

    logger.debug("Dropping the JSONB history and artifacts columns")
    op.drop_index(ARTIFACTS_GIN_INDEX, table_name=TASKS_TABLE)
    op.drop_column(TASKS_TABLE, "artifacts")
    op.drop_column(TASKS_TABLE, "history")


def downgrade() -> None:
    """Restore the JSONB columns, drop the child tables, claims, indexes, and projections."""
    logger.debug("Restoring the JSONB history and artifacts columns")
    op.add_column(TASKS_TABLE, sa.Column("artifacts", JSONB(), nullable=True))
    op.add_column(TASKS_TABLE, sa.Column("history", JSONB(), nullable=True))
    op.create_index(
        ARTIFACTS_GIN_INDEX,
        TASKS_TABLE,
        ["artifacts"],
        postgresql_using="gin",
    )

    logger.debug("Folding task_messages and task_artifacts back into the JSONB columns")
    op.execute(
        f"""
        UPDATE {TASKS_TABLE} t
           SET history = ordered.history
          FROM (
               SELECT task_id, jsonb_agg(payload ORDER BY seq) AS history
                 FROM {TASK_MESSAGES_TABLE}
                GROUP BY task_id
           ) AS ordered
         WHERE ordered.task_id = t.id
        """
    )
    op.execute(
        f"""
        UPDATE {TASKS_TABLE} t
           SET artifacts = grouped.artifacts
          FROM (
               SELECT task_id, jsonb_agg(payload ORDER BY artifact_id) AS artifacts
                 FROM {TASK_ARTIFACTS_TABLE}
                GROUP BY task_id
           ) AS grouped
         WHERE grouped.task_id = t.id
        """
    )

    logger.debug("Dropping task_artifacts and task_messages")
    op.drop_table(TASK_ARTIFACTS_TABLE)
    op.drop_index(MESSAGE_ID_INDEX, table_name=TASK_MESSAGES_TABLE)
    op.drop_table(TASK_MESSAGES_TABLE)

    logger.debug("Dropping task ownership claims table")
    op.drop_index("task_claims_expiry_idx", table_name=TASK_CLAIMS_TABLE)
    op.drop_table(TASK_CLAIMS_TABLE)

    logger.debug("Restoring ix_tasks_context_id, dropped by this migration's upgrade")
    op.create_index(CONTEXT_ID_INDEX, TASKS_TABLE, ["context_id"])

    logger.debug("Dropping tasks list-order, state, and agent/context indexes")
    op.drop_index(AGENT_CONTEXT_INDEX, table_name=TASKS_TABLE)
    op.drop_index(ACTIVE_STATE_INDEX, table_name=TASKS_TABLE)
    op.drop_index(STATE_INDEX, table_name=TASKS_TABLE)
    op.drop_index(LIST_ORDER_INDEX, table_name=TASKS_TABLE)

    logger.debug("Dropping agent_id, generated task state, and status timestamp columns")
    op.execute(
        f"""
        ALTER TABLE {TASKS_TABLE}
            DROP COLUMN agent_id,
            DROP COLUMN status_timestamp,
            DROP COLUMN state
        """
    )

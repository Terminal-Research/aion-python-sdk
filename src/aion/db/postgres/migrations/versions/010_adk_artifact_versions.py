"""Give every version number of an ADK artifact once, across the servers of an agent.

The ADK artifact service numbers the versions of an artifact per agent,
owner, context and name: ``app_name``, ``user_id``, ``session_id`` and
``filename`` in ADK's terms, the agent id, the owner scope, the context ID and
the artifact name in the ``tasks`` and ``task_artifacts`` tables it reads them
back from. A version reaches ``task_artifacts`` only later, inside the task
that carries it, and no table holds that key with the version as columns, so
nothing kept two servers saving the same artifact at once from giving both
saves one number.

``adk_artifact_versions`` holds one row per number given, keyed by
``(agent_id, owner_scope, context_id, artifact_name, version)``: the second
insert of a number conflicts, and the server that made it numbers again from
what the database holds.

The table and column names are written out here rather than imported: this
revision has to describe the schema as it was when it ran.

Revision ID: 010
Revises: 009
"""

from alembic import op
import sqlalchemy as sa


revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None

_TABLE = "adk_artifact_versions"


def upgrade() -> None:
    """Create the table of given ADK artifact version numbers."""
    op.create_table(
        _TABLE,
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("owner_scope", sa.Text(), nullable=False),
        sa.Column("context_id", sa.Text(), nullable=False),
        sa.Column("artifact_name", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.PrimaryKeyConstraint("agent_id", "owner_scope", "context_id", "artifact_name", "version"),
    )


def downgrade() -> None:
    """Drop the table and the numbers it holds."""
    op.drop_table(_TABLE)

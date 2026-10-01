"""Reserve each context for the one holder admitted into it first; close every context that already exists.

A context is private to one caller (``owner_scope``) or shared by one Aion
gateway conversation (the receiving agent identity and the edge environment).
Task rows cannot say which: a context holds tasks of several initiators, and
they come and go while the framework state keyed by the context stays. The
reservation is written by the first request admitted into the context, before
any task or state exists, and is never updated or deleted.

A context that already has data when this revision runs was used before
reservations existed, and nothing it left says who may continue it: its owners
were recorded per task, never per conversation, and the gateway coordinates of
a shared conversation were never recorded at all. So every such context of an
agent is reserved ``blocked`` - nobody is admitted into it again. Its tasks,
checkpoints and sessions stay where they are, readable by their owners as
before; only new messages into it are refused. The data is found in:

* ``tasks``: ``agent_id`` and ``context_id``;
* the LangGraph checkpoint tables, when the agent kept its checkpoints in this
  database: a ``thread_id`` of the form ``aion.v1:<agent>:<owner>:<context>``,
  each part percent-encoded;
* the ADK ``sessions`` table, when the agent kept its sessions here:
  ``app_name`` is the agent and ``id`` the context, for every session not
  saved under the shared legacy user.

State saved before it carried the agent - a checkpoint whose ``thread_id`` is
the context ID alone, an ADK session under the shared ``default-user`` - names
no agent to reserve for. The server looks for it when a context is reserved
for the first time and refuses the context then.

The schema, table and key names are written out here rather than imported:
this revision has to read the data as it was when it ran.

Revision ID: 006
Revises: 005
"""

from urllib.parse import unquote

from alembic import op
import sqlalchemy as sa


revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None

_TABLE = "context_reservations"
_LANGGRAPH_TABLES = ("aion_langgraph.checkpoints", "aion_langgraph.checkpoint_blobs", "aion_langgraph.checkpoint_writes")
_ADK_SESSIONS = "aion_adk.sessions"
_ADK_LEGACY_USER_ID = "default-user"
_STATE_KEY_PREFIX = "aion.v1"


def upgrade() -> None:
    """Create the context reservations table and close the contexts that already exist."""
    op.create_table(
        _TABLE,
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("context_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("owner_scope", sa.Text(), nullable=True),
        sa.Column("owner_agent_identity_id", sa.Text(), nullable=True),
        sa.Column("edge_agent_environment_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.PrimaryKeyConstraint("agent_id", "context_id"),
        sa.CheckConstraint(
            "(kind = 'private' AND owner_scope IS NOT NULL AND owner_scope <> ''"
            " AND owner_agent_identity_id IS NULL AND edge_agent_environment_id IS NULL)"
            " OR (kind = 'shared' AND owner_scope IS NULL"
            " AND owner_agent_identity_id IS NOT NULL AND edge_agent_environment_id IS NOT NULL)"
            " OR (kind = 'blocked' AND owner_scope IS NULL"
            " AND owner_agent_identity_id IS NULL AND edge_agent_environment_id IS NULL)",
            name="context_reservations_holder_shape",
        ),
    )

    connection = op.get_bind()
    contexts = _task_contexts(connection) | _langgraph_contexts(connection) | _adk_contexts(connection)
    if contexts:
        connection.execute(
            sa.text(
                f"INSERT INTO {_TABLE} (agent_id, context_id, kind) VALUES (:agent_id, :context_id, 'blocked') "
                "ON CONFLICT (agent_id, context_id) DO NOTHING"
            ),
            [{"agent_id": agent_id, "context_id": context_id} for agent_id, context_id in sorted(contexts)],
        )


def downgrade() -> None:
    """Drop the context reservations table."""
    op.drop_table(_TABLE)


def _task_contexts(connection) -> set[tuple[str, str]]:
    rows = connection.execute(
        sa.text("SELECT DISTINCT agent_id, context_id FROM tasks WHERE agent_id IS NOT NULL AND context_id IS NOT NULL")
    )
    return {(agent_id, context_id) for agent_id, context_id in rows if agent_id and context_id}


def _langgraph_contexts(connection) -> set[tuple[str, str]]:
    contexts: set[tuple[str, str]] = set()
    for table in _existing(connection, _LANGGRAPH_TABLES):
        rows = connection.execute(
            sa.text(f"SELECT DISTINCT thread_id FROM {table} WHERE thread_id LIKE :prefix"),
            {"prefix": f"{_STATE_KEY_PREFIX}:%"},
        )
        for (thread_id,) in rows:
            parts = thread_id.split(":")
            if len(parts) != 4:
                continue
            agent_id, _owner, context_id = (unquote(part) for part in parts[1:])
            if agent_id and context_id:
                contexts.add((agent_id, context_id))
    return contexts


def _adk_contexts(connection) -> set[tuple[str, str]]:
    if not _existing(connection, (_ADK_SESSIONS,)):
        return set()
    rows = connection.execute(
        sa.text(f"SELECT DISTINCT app_name, id FROM {_ADK_SESSIONS} WHERE user_id <> :legacy_user"),
        {"legacy_user": _ADK_LEGACY_USER_ID},
    )
    return {(agent_id, context_id) for agent_id, context_id in rows if agent_id and context_id}


def _existing(connection, tables: tuple[str, ...]) -> list[str]:
    """Those of ``tables`` that exist: a framework the agents do not use has none."""
    return [
        table
        for table in tables
        if connection.execute(sa.text("SELECT to_regclass(:table)"), {"table": table}).scalar() is not None
    ]

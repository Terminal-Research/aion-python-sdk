"""Record which callers are bound to each context, and give contexts a deletion lifecycle.

``context_bindings`` holds one row per caller admitted into a context: the
agent, the context and the caller's ``owner_scope``. A private context has one
binding, its holder; a shared gateway conversation has one per participant. A
binding is what makes a context visible to a caller through the Context
extension, and ``DeleteContext`` removes it.

``context_reservations`` gains the context's lifecycle:

* ``state`` - ``active``, ``deleting`` (removing the last binding started a
  deletion: no new work is admitted while it settles) or ``deleted`` (nothing
  of the context remains, and the next request reserves it afresh);
* ``deletion_operation_id`` and ``deletion_requested_by`` - the durable
  deletion and the caller whose request started it, which is the caller a
  retry resumes it for;
* ``deleted_at`` - when the deletion finished.

Existing contexts get one binding per owner that has tasks in them, so every
caller keeps reading what it could read before. Contexts reserved ``blocked``
are included: their owners still read their tasks, they just cannot continue
them. Tasks of unattributed invocations are not: their owner is the subject
every anonymous arrival shares, which no caller has individual access by, so a
binding for it could never be removed.

The table and column names are written out here rather than imported: this
revision has to describe the schema as it was when it ran.

Revision ID: 007
Revises: 006
"""

from alembic import op
import sqlalchemy as sa


revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None

_RESERVATIONS = "context_reservations"
_BINDINGS = "context_bindings"
_PRINCIPAL_PREFIX = "aion:v1:"


def upgrade() -> None:
    """Add the context lifecycle columns and the bindings table, then bind existing owners."""
    op.add_column(
        _RESERVATIONS,
        sa.Column("state", sa.Text(), nullable=False, server_default=sa.text("'active'")),
    )
    op.add_column(_RESERVATIONS, sa.Column("deletion_operation_id", sa.Text(), nullable=True))
    op.add_column(_RESERVATIONS, sa.Column("deletion_requested_by", sa.Text(), nullable=True))
    op.add_column(_RESERVATIONS, sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "context_reservations_state",
        _RESERVATIONS,
        "(state = 'active' AND deletion_operation_id IS NULL AND deletion_requested_by IS NULL"
        " AND deleted_at IS NULL)"
        " OR (state = 'deleting' AND deletion_operation_id IS NOT NULL AND deleted_at IS NULL)"
        " OR (state = 'deleted' AND deleted_at IS NOT NULL)",
    )

    op.create_table(
        _BINDINGS,
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("context_id", sa.Text(), nullable=False),
        sa.Column("owner_scope", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.PrimaryKeyConstraint("agent_id", "context_id", "owner_scope"),
        sa.ForeignKeyConstraint(
            ["agent_id", "context_id"],
            [f"{_RESERVATIONS}.agent_id", f"{_RESERVATIONS}.context_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("owner_scope <> ''", name="context_bindings_owner_scope"),
    )
    op.create_index(
        "ix_context_bindings_agent_owner",
        _BINDINGS,
        ["agent_id", "owner_scope"],
    )

    op.execute(
        sa.text(
            f"INSERT INTO {_BINDINGS} (agent_id, context_id, owner_scope, created_at) "
            "SELECT t.agent_id, t.context_id, t.owner_scope, MIN(t.created_at) "
            "FROM tasks t "
            f"JOIN {_RESERVATIONS} r ON r.agent_id = t.agent_id AND r.context_id = t.context_id "
            "WHERE t.owner_scope IS NOT NULL AND t.owner_scope <> '' "
            "AND t.owner_scope NOT LIKE :shared_anonymous "
            "GROUP BY t.agent_id, t.context_id, t.owner_scope "
            "ON CONFLICT DO NOTHING"
        ).bindparams(shared_anonymous=f"{_PRINCIPAL_PREFIX}ExternalAnonymous:%")
    )


def downgrade() -> None:
    """Drop the bindings table and the lifecycle columns."""
    op.drop_index("ix_context_bindings_agent_owner", table_name=_BINDINGS)
    op.drop_table(_BINDINGS)
    op.drop_constraint("context_reservations_state", _RESERVATIONS, type_="check")
    op.drop_column(_RESERVATIONS, "deleted_at")
    op.drop_column(_RESERVATIONS, "deletion_requested_by")
    op.drop_column(_RESERVATIONS, "deletion_operation_id")
    op.drop_column(_RESERVATIONS, "state")

"""Keep a2a-sdk's push notification config table under the SDK migrations.

``push_notification_configs`` holds the webhook configs that a2a-sdk's
``DatabasePushNotificationConfigStore`` reads and writes. Its columns are
a2a-sdk's own (``PushNotificationConfigMixin`` in ``a2a.server.models``): the
table as a2a-sdk's store creates it, plus ``owner`` and its index from revision
``6419d2d130f6`` of ``a2a/migrations`` and ``protocol_version`` from revision
``38ce57e08137``. The "Database migrations" section of
``docs/development/a2a-sdk-mapping.md`` maps every a2a-sdk revision to the SDK
revision that applies it.

On a database where a2a-sdk's store has already created the table, this
revision adds only what the table lacks, as those two a2a-sdk revisions do:
``owner`` filled with a2a-sdk's legacy owner for the rows it finds, its index,
and ``protocol_version``. The legacy owner is a value for existing rows only;
the column keeps no default afterwards, as in a2a-sdk's model.

The table, column and index names are written out here rather than imported:
this revision has to describe the schema as it was when it ran.

Revision ID: 009
Revises: 008
"""

from alembic import op
import sqlalchemy as sa


revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None

_TABLE = "push_notification_configs"
_OWNER_INDEX = f"ix_{_TABLE}_owner"
_LEGACY_OWNER = "legacy_v03_no_user_info"


def upgrade() -> None:
    """Create the push notification config table, or add what an existing one lacks."""
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(_TABLE):
        op.create_table(
            _TABLE,
            sa.Column("task_id", sa.String(36), primary_key=True),
            sa.Column("config_id", sa.String(255), primary_key=True),
            sa.Column("config_data", sa.LargeBinary(), nullable=False),
            sa.Column("owner", sa.String(255), nullable=True),
            sa.Column("protocol_version", sa.String(16), nullable=True),
        )
        op.create_index(_OWNER_INDEX, _TABLE, ["owner"])
        return

    columns = {column["name"] for column in inspector.get_columns(_TABLE)}
    if "owner" not in columns:
        op.add_column(
            _TABLE,
            sa.Column("owner", sa.String(255), nullable=True, server_default=_LEGACY_OWNER),
        )
        op.alter_column(_TABLE, "owner", server_default=None)
    if _OWNER_INDEX not in {index["name"] for index in inspector.get_indexes(_TABLE)}:
        op.create_index(_OWNER_INDEX, _TABLE, ["owner"])
    if "protocol_version" not in columns:
        op.add_column(_TABLE, sa.Column("protocol_version", sa.String(16), nullable=True))


def downgrade() -> None:
    """Drop the push notification config table and the configs it holds."""
    op.drop_index(_OWNER_INDEX, table_name=_TABLE)
    op.drop_table(_TABLE)

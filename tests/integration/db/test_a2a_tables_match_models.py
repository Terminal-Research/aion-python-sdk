"""The tables the SDK migrations build on a2a-sdk's models match those models.

a2a-sdk's stores read and write ``task_versions``, ``task_events`` and
``push_notification_configs`` through its own SQLAlchemy models, while the
tables themselves come from the SDK migrations (``008`` and ``009``). A test
database that a store created from the model would always agree with it; this
one is built by the migrations alone, so an a2a-sdk upgrade that changes a
model without an SDK revision to match fails here. The "Database migrations"
section of ``docs/development/a2a-sdk-mapping.md`` is the registry of those
revisions.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from a2a.server.models import PushNotificationConfigModel, TaskEventModel, TaskVersionModel
from sqlalchemy.dialects import postgresql

from aion.db.postgres.constants import AION_SCHEMA

MODELS = (TaskVersionModel, TaskEventModel, PushNotificationConfigModel)
DIALECT = postgresql.dialect()


def _shape_of_model(table: sa.Table) -> dict:
    return {
        "columns": {
            column.name: (column.type.compile(dialect=DIALECT), column.nullable)
            for column in table.columns
        },
        "primary_key": sorted(column.name for column in table.primary_key.columns),
        "indexes": sorted(
            (index.name, tuple(column.name for column in index.columns)) for index in table.indexes
        ),
    }


def _shape_in_database(connection: sa.Connection, name: str) -> dict:
    inspector = sa.inspect(connection)
    columns = inspector.get_columns(name, schema=AION_SCHEMA)
    return {
        "columns": {
            column["name"]: (column["type"].compile(dialect=DIALECT), column["nullable"])
            for column in columns
        },
        "primary_key": sorted(
            inspector.get_pk_constraint(name, schema=AION_SCHEMA)["constrained_columns"]
        ),
        "indexes": sorted(
            (index["name"], tuple(index["column_names"]))
            for index in inspector.get_indexes(name, schema=AION_SCHEMA)
        ),
        "defaults": {
            column["name"]: column["default"]
            for column in columns
            if column["default"] is not None and not column.get("autoincrement")
        },
    }


@pytest.mark.parametrize("model", MODELS, ids=lambda model: model.__tablename__)
async def test_the_migrated_table_matches_a2a_sdks_model(postgres_engine, model) -> None:
    table = model.__table__
    async with postgres_engine.connect() as connection:
        shape = await connection.run_sync(_shape_in_database, table.name)

    defaults = shape.pop("defaults")
    assert shape == _shape_of_model(table)
    assert defaults == {}, f"server defaults a2a-sdk's model does not declare: {defaults}"

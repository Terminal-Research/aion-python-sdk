"""The SDK's own models describe the tables its migrations build.

The repositories read and write through ``aion.db.postgres.models``, while the
tables come from the Alembic chain alone. A column added to a model without a
migration, or a migration that changes a column the model still describes the
old way, would surface only when a query fails. Alembic's own comparison runs
here instead, between the models and a database built by the migrations.

Indexes and CHECK constraints belong to the migrations: the models leave them
out, so the database holding more of them is expected. A model that claims an
index the migrations do not build is drift, and fails like a column does.
"""

from __future__ import annotations

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext

from aion.db.postgres.models import BaseModel

DRIFT = {
    "add_table",
    "remove_table",
    "add_column",
    "remove_column",
    "modify_type",
    "modify_nullable",
    "modify_default",
    "add_index",
    "add_fk",
    "remove_fk",
}
"""Differences that mean a model and the migrations disagree."""


def _differences(connection) -> list[tuple]:
    tables = set(BaseModel.metadata.tables)
    context = MigrationContext.configure(
        connection,
        opts={
            "compare_type": True,
            "compare_server_default": True,
            "include_name": lambda name, kind, _parent: kind != "table" or name in tables,
        },
    )
    flat = []
    for difference in compare_metadata(context, BaseModel.metadata):
        flat.extend(difference if isinstance(difference, list) else [difference])
    return [difference for difference in flat if difference[0] in DRIFT]


async def test_the_models_and_the_migrations_describe_the_same_tables(postgres_engine) -> None:
    async with postgres_engine.connect() as connection:
        differences = await connection.run_sync(_differences)

    assert differences == []

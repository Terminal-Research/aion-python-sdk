"""Migration 009 on a database where a2a-sdk's store already made the push config table.

Before the table was in the SDK migrations, a2a-sdk's store created it from
whatever model the installed a2a-sdk had. 009 has to take such a table over:
keep its rows, and add what a2a-sdk's later revisions added to it.

Set ``POSTGRES_TEST_URL`` to enable these tests.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text

from aion.db.postgres.constants import AION_SCHEMA
from aion.db.postgres.migrations.env import config as alembic_config
from aion.db.postgres.utils import convert_pg_url
from aion.db.settings import db_settings

pytestmark = [pytest.mark.timeout(120)]

TABLE = "push_notification_configs"


@pytest.fixture
def at_revision_008():
    """The schema at 008 with no push config table; restored to head afterwards."""
    test_url = os.getenv("POSTGRES_TEST_URL")
    if not test_url:
        pytest.skip("POSTGRES_TEST_URL is not set")
    db_settings.pg_url = test_url
    sync_url = convert_pg_url(test_url, driver="psycopg")
    alembic_config.set_main_option("sqlalchemy.url", sync_url)
    if not getattr(alembic_config, "cmd_opts", None):
        alembic_config.cmd_opts = SimpleNamespace()
    engine = create_engine(sync_url, connect_args={"options": f"-csearch_path={AION_SCHEMA}"})
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "008")
    try:
        yield engine
    finally:
        # Back to 008 first (009's downgrade drops the table), then whatever a
        # failed test left behind at 008, then forward again.
        command.downgrade(alembic_config, "008")
        with engine.begin() as connection:
            connection.execute(text(f"DROP TABLE IF EXISTS {AION_SCHEMA}.{TABLE}"))
        command.upgrade(alembic_config, "head")
        engine.dispose()


def test_a_table_without_owner_keeps_its_rows_and_gains_the_later_columns(at_revision_008):
    engine = at_revision_008
    with engine.begin() as connection:
        connection.execute(text(
            f"CREATE TABLE {TABLE} (task_id VARCHAR(36) NOT NULL, config_id VARCHAR(255) NOT NULL, "
            "config_data BYTEA NOT NULL, PRIMARY KEY (task_id, config_id))"
        ))
        connection.execute(
            text(f"INSERT INTO {TABLE} (task_id, config_id, config_data) VALUES ('task-1', 'config-1', 'x')")
        )

    command.upgrade(alembic_config, "009")

    with engine.begin() as connection:
        row = connection.execute(text(f"SELECT owner, protocol_version FROM {TABLE}")).one()
        inspector = inspect(connection)
        columns = {column["name"]: column for column in inspector.get_columns(TABLE, schema=AION_SCHEMA)}
        indexes = {index["name"] for index in inspector.get_indexes(TABLE, schema=AION_SCHEMA)}

    assert row.owner == "legacy_v03_no_user_info"
    assert row.protocol_version is None
    assert columns["owner"]["default"] is None, "the legacy owner is for existing rows only"
    assert "ix_push_notification_configs_owner" in indexes


def test_a_table_a2a_sdk_made_from_its_current_model_is_taken_over_as_is(at_revision_008):
    from a2a.server.models import PushNotificationConfigModel

    engine = at_revision_008
    with engine.begin() as connection:
        PushNotificationConfigModel.__table__.create(connection)
        connection.execute(text(
            f"INSERT INTO {TABLE} (task_id, config_id, config_data, owner) "
            "VALUES ('task-1', 'config-1', 'x', 'alice')"
        ))

    command.upgrade(alembic_config, "009")

    with engine.begin() as connection:
        owner = connection.execute(text(f"SELECT owner FROM {TABLE}")).scalar_one()
    assert owner == "alice"

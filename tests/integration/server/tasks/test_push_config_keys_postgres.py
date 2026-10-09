"""Rotating ENCRYPTION_KEY over push notification configs stored in PostgreSQL.

Each store here is what a server builds from one value of ``ENCRYPTION_KEY``,
over the one migrated database the servers share.
"""

from __future__ import annotations

import pytest
from a2a.server.context import ServerCallContext
from a2a.types.a2a_pb2 import TaskPushNotificationConfig
from cryptography.fernet import Fernet
from sqlalchemy import text

from .postgres_support import POSTGRES_TEST_URL, prepared_database

from aion.db.postgres.constants import AION_SCHEMA  # noqa: E402 - after postgres_support
from aion.server.tasks.push_config_store import AionDatabasePushNotificationConfigStore  # noqa: E402

pytestmark = pytest.mark.skipif(not POSTGRES_TEST_URL, reason="POSTGRES_TEST_URL is not set")

OLD = Fernet.generate_key().decode()
NEW = Fernet.generate_key().decode()
CALLER = ServerCallContext()


@pytest.fixture
async def db():
    async with prepared_database() as manager:
        async with manager.get_session() as session:
            await session.execute(text("TRUNCATE push_notification_configs"))
            await session.commit()
        yield manager


def _store(db, *keys: str) -> AionDatabasePushNotificationConfigStore:
    engine = db.get_engine().execution_options(schema_translate_map={None: AION_SCHEMA})
    return AionDatabasePushNotificationConfigStore(engine=engine, encryption_keys=keys)


def _config(config_id: str) -> TaskPushNotificationConfig:
    return TaskPushNotificationConfig(id=config_id, url=f"https://example.com/{config_id}")


async def test_a_rotated_server_delivers_to_configs_written_before_the_rotation(db) -> None:
    await _store(db, OLD).set_info("task-1", _config("before"), CALLER)
    rotated = _store(db, NEW, OLD)
    await rotated.set_info("task-1", _config("after"), CALLER)

    configs = await rotated.get_info_for_dispatch("task-1")

    assert sorted(config.id for config in configs) == ["after", "before"]


async def test_a_config_under_a_dropped_key_is_skipped_and_the_rest_delivered(db) -> None:
    """a2a-sdk logs an unreadable config and returns the task's others: the
    task does not fail because one of its webhooks cannot be read."""
    await _store(db, OLD).set_info("task-1", _config("before"), CALLER)
    await _store(db, NEW, OLD).set_info("task-1", _config("after"), CALLER)

    configs = await _store(db, NEW).get_info_for_dispatch("task-1")

    assert [config.id for config in configs] == ["after"]

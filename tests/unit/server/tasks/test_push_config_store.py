"""Encryption keys of the database push notification config store.

The store hands a2a-sdk a ``MultiFernet`` in place of the single key a2a-sdk
expects. These tests go through a2a-sdk's own row conversion, so an a2a-sdk
release that starts using the key some other way fails here rather than on a
rotated deployment.
"""

from unittest.mock import Mock

import pytest
from a2a.types.a2a_pb2 import TaskPushNotificationConfig
from cryptography.fernet import Fernet

from aion.server.tasks.push_config_store import AionDatabasePushNotificationConfigStore

OLD = Fernet.generate_key().decode()
NEW = Fernet.generate_key().decode()


def store(*keys: str) -> AionDatabasePushNotificationConfigStore:
    return AionDatabasePushNotificationConfigStore(engine=Mock(), encryption_keys=keys)


def written_by(writer: AionDatabasePushNotificationConfigStore):
    config = TaskPushNotificationConfig(id="config-1", url="https://example.com/hook")
    return writer._to_orm("task-1", config, "owner-1")


def test_a_rotated_store_reads_configs_written_under_the_old_key():
    row = written_by(store(OLD))

    assert store(NEW, OLD)._from_orm(row).url == "https://example.com/hook"


def test_the_first_key_encrypts():
    row = written_by(store(NEW, OLD))

    assert Fernet(NEW.encode()).decrypt(row.config_data)


def test_a_config_under_a_key_no_longer_listed_is_unreadable():
    """a2a-sdk skips such a row when it reads a task's configs; see open-items.md."""
    row = written_by(store(OLD))

    with pytest.raises(ValueError):
        store(NEW)._from_orm(row)


def test_no_keys_store_plaintext():
    row = written_by(store())

    assert b"https://example.com/hook" in row.config_data

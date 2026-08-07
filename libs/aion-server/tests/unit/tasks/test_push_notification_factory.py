"""Tests for PushNotificationFactory.

The factory decides three things the push channel depends on and nothing else
asserts: which sender the server dispatches through — the SDK base class sends
webhook calls anonymously — how long a delivery may wait for the receiver, which
httpx otherwise defaults to five seconds for, and whether stored configs, which
carry webhook credentials, are encrypted at rest.
"""

import pytest
from a2a.server.tasks import InMemoryPushNotificationConfigStore
from cryptography.fernet import Fernet
from unittest.mock import Mock, patch

from aion.server.tasks.authenticated_push_sender import AuthenticatedPushNotificationSender
from aion.server.tasks.push_notifications import PushNotificationFactory

STORE_PATH = (
    "a2a.server.tasks.database_push_notification_config_store."
    "DatabasePushNotificationConfigStore"
)


@pytest.fixture
def db_manager() -> Mock:
    """An initialized db manager stand-in exposing a chainable engine."""
    manager = Mock()
    manager.is_initialized = True
    manager.get_engine.return_value.execution_options.return_value = Mock()
    return manager


class TestSenderSelection:
    """The server must dispatch through the sender that honours declared credentials."""

    def test_sender_authenticates_deliveries(self):
        """A config declaring authentication is useless unless this sender is wired in."""
        config_store, sender = PushNotificationFactory.create()

        assert isinstance(sender, AuthenticatedPushNotificationSender)
        assert isinstance(config_store, InMemoryPushNotificationConfigStore)

    def test_sender_reads_from_the_store_it_returns(self):
        """Dispatch resolves configs through the same store the handler writes to."""
        config_store, sender = PushNotificationFactory.create()

        assert sender._config_store is config_store

    def test_uninitialized_db_falls_back_to_memory(self):
        """A db manager that never came up must not take the postgres path."""
        manager = Mock()
        manager.is_initialized = False

        config_store, _ = PushNotificationFactory.create(manager)

        assert isinstance(config_store, InMemoryPushNotificationConfigStore)

    def test_initialized_db_takes_the_postgres_path(self, db_manager):
        """With a live database the configs must outlive the process."""
        with patch(STORE_PATH) as store_cls:
            config_store, _ = PushNotificationFactory.create(db_manager)

        assert config_store is store_cls.return_value


class TestDeliveryTimeout:
    """httpx defaults every phase to 5s, too short for a webhook that works before answering."""

    def test_read_timeout_comes_from_settings(self, monkeypatch):
        """The configured budget is what waiting on the webhook response gets."""
        monkeypatch.setattr(
            "aion.server.tasks.push_notifications.app_settings.push_notification_timeout_seconds",
            45.0,
        )

        _, sender = PushNotificationFactory.create()

        assert sender._client.timeout.read == 45.0
        assert sender._client.timeout.write == 45.0

    def test_connect_stays_short(self, monkeypatch):
        """An unreachable host must fail fast: the first delivery blocks the request path."""
        monkeypatch.setattr(
            "aion.server.tasks.push_notifications.app_settings.push_notification_timeout_seconds",
            45.0,
        )

        _, sender = PushNotificationFactory.create()

        assert sender._client.timeout.connect == 5.0

    def test_default_exceeds_the_httpx_default(self):
        """The whole point of the setting is to not ship httpx's 5 seconds."""
        _, sender = PushNotificationFactory.create()

        assert sender._client.timeout.read > 5.0


class TestConfigEncryption:
    """Stored configs carry webhook credentials, so the key must reach the store."""

    KEY_SETTING = "aion.server.tasks.push_notifications.app_settings.encryption_key"

    def test_configured_key_reaches_the_store(self, db_manager, monkeypatch):
        """Without this wiring the key is set in the environment and ignored."""
        key = Fernet.generate_key().decode()
        monkeypatch.setattr(self.KEY_SETTING, key)

        with patch(STORE_PATH) as store_cls:
            PushNotificationFactory.create(db_manager)

        assert store_cls.call_args.kwargs["encryption_key"] == key

    def test_unset_key_leaves_encryption_off(self, db_manager, monkeypatch):
        """Encryption is opt-in: an absent key must not become a literal value."""
        monkeypatch.setattr(self.KEY_SETTING, None)

        with patch(STORE_PATH) as store_cls:
            PushNotificationFactory.create(db_manager)

        assert store_cls.call_args.kwargs["encryption_key"] is None

    def test_key_travels_through_a_real_store(self, db_manager, monkeypatch):
        """Pins the argument name against an SDK that renames or drops it."""
        monkeypatch.setattr(self.KEY_SETTING, Fernet.generate_key().decode())

        config_store, _ = PushNotificationFactory.create(db_manager)

        assert config_store._fernet is not None

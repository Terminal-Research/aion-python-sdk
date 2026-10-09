"""Tests for PushNotificationFactory.

The factory decides four things the push channel depends on and nothing else
asserts: which sender the server dispatches through — the SDK base class sends
webhook calls anonymously — how long a delivery may wait for the receiver, which
httpx otherwise defaults to five seconds for, whether stored configs, which
carry webhook credentials, are encrypted at rest, and whose configs they are.
"""

import pytest
from a2a.server.owner_resolver import resolve_user_scope
from a2a.server.tasks import InMemoryPushNotificationConfigStore
from cryptography.fernet import Fernet
from unittest.mock import AsyncMock, Mock, patch

from aion.server.tasks.push_sender import AionPushNotificationSender
from aion.server.tasks.push_notifications import PushNotificationFactory

STORE_PATH = (
    "aion.server.tasks.push_config_store."
    "AionDatabasePushNotificationConfigStore"
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

        assert isinstance(sender, AionPushNotificationSender)
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
    """Stored configs carry webhook credentials, so the keys must reach the store."""

    KEY_SETTING = "aion.server.tasks.push_notifications.app_settings.encryption_key"

    def test_configured_keys_reach_the_store(self, db_manager, monkeypatch):
        """Without this wiring the keys are set in the environment and ignored."""
        new, old = Fernet.generate_key().decode(), Fernet.generate_key().decode()
        monkeypatch.setattr(self.KEY_SETTING, f"{new},{old}")

        with patch(STORE_PATH) as store_cls:
            PushNotificationFactory.create(db_manager)

        assert store_cls.call_args.kwargs["encryption_keys"] == (new, old)

    def test_unset_key_leaves_encryption_off(self, db_manager, monkeypatch):
        """Encryption is opt-in: an absent key must not become a literal value."""
        monkeypatch.setattr(self.KEY_SETTING, None)

        with patch(STORE_PATH) as store_cls:
            PushNotificationFactory.create(db_manager)

        assert store_cls.call_args.kwargs["encryption_keys"] == ()

    def test_keys_travel_through_a_real_store(self, db_manager, monkeypatch):
        """Pins the argument name against a store that renames or drops it."""
        monkeypatch.setattr(self.KEY_SETTING, Fernet.generate_key().decode())

        config_store, _ = PushNotificationFactory.create(db_manager)

        assert config_store._fernet is not None


class TestConfigOwner:
    """A task's configs belong to the task's owner, resolved as the agent resolves it."""

    @staticmethod
    def resolver(context):
        return "owner-from-the-agent"

    def test_the_memory_store_resolves_owners_like_the_agent(self):
        config_store, _ = PushNotificationFactory.create(owner_resolver=self.resolver)

        assert config_store.owner_resolver is self.resolver

    def test_the_database_store_resolves_owners_like_the_agent(self, db_manager):
        """Without it every config would sit under one owner, whoever the task belongs to."""
        config_store, _ = PushNotificationFactory.create(db_manager, owner_resolver=self.resolver)

        assert config_store.owner_resolver is self.resolver

    def test_the_default_is_the_users_name(self):
        config_store, _ = PushNotificationFactory.create()

        assert config_store.owner_resolver is resolve_user_scope


class TestPrepare:
    """The database table is created while the server starts, not in a request."""

    async def test_a_database_store_creates_its_table(self):
        from aion.server.tasks.push_config_store import AionDatabasePushNotificationConfigStore

        store = Mock(spec=AionDatabasePushNotificationConfigStore)
        store.initialize = AsyncMock()

        await PushNotificationFactory.prepare(store)

        store.initialize.assert_awaited_once_with()

    async def test_a_memory_store_needs_nothing(self):
        config_store, _ = PushNotificationFactory.create()

        await PushNotificationFactory.prepare(config_store)

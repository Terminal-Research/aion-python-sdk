"""Tests that the store manager picks a2a-sdk's cluster or single-process mode."""

from unittest.mock import Mock, patch

from a2a.server.cluster import DatabaseTaskEventStream

from aion.server.tasks.store_manager import StoreManager
from aion.server.tasks.stores.in_memory_task_store import InMemoryTaskStore
from aion.server.tasks.stores.postgres_task_store import PostgresTaskStore
from aion.server.tasks.stores.postgres_versioned_task_store import PostgresVersionedTaskStore


def test_without_a_database_the_handler_gets_the_plain_in_memory_store() -> None:
    """Single-process mode: no versions and no event stream, as in a2a-sdk."""
    manager = StoreManager()

    with patch("aion.server.tasks.store_manager.db_manager", Mock(is_initialized=False)):
        manager.initialize("test-agent")

    assert isinstance(manager.get_store(), InMemoryTaskStore)
    assert manager.get_handler_store() is manager.get_store()
    assert manager.get_event_stream() is None


def test_with_a_database_the_handler_gets_the_versioned_store_and_the_stream() -> None:
    """Cluster mode: a versioned store over the Postgres store, and a2a-sdk's stream."""
    manager = StoreManager()
    db_manager = Mock(is_initialized=True)

    with patch("aion.server.tasks.store_manager.db_manager", db_manager):
        manager.initialize("test-agent")

    handler_store = manager.get_handler_store()
    assert isinstance(manager.get_store(), PostgresTaskStore)
    assert isinstance(handler_store, PostgresVersionedTaskStore)
    assert handler_store.as_task_store is manager.get_store()
    stream = manager.get_event_stream()
    assert isinstance(stream, DatabaseTaskEventStream)
    assert stream._engine is db_manager.get_engine.return_value
    assert stream._create_table is False

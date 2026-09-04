"""Tests that task storage and ownership enforcement are selected together."""

from unittest.mock import Mock, patch

from aion.server.tasks.ownership import DegenerateOwnershipProvider, PostgresOwnershipProvider
from aion.server.tasks.store_manager import StoreManager
from aion.server.tasks.stores.in_memory_task_store import InMemoryTaskStore
from aion.server.tasks.stores.postgres_task_store import PostgresTaskStore


def test_uninitialized_database_selects_in_memory_pair() -> None:
    """The development store is always paired with the degenerate provider."""
    manager = StoreManager()

    with patch("aion.server.tasks.store_manager.db_manager", Mock(is_initialized=False)):
        manager.initialize("test-agent")

    assert isinstance(manager.get_store(), InMemoryTaskStore)
    assert isinstance(manager.get_ownership_provider(), DegenerateOwnershipProvider)
    assert manager.get_store().ownership_provider is manager.get_ownership_provider()


def test_initialized_database_selects_postgres_pair() -> None:
    """The production store is always paired with the fenced provider."""
    manager = StoreManager()

    with patch("aion.server.tasks.store_manager.db_manager", Mock(is_initialized=True)):
        manager.initialize("test-agent")

    assert isinstance(manager.get_store(), PostgresTaskStore)
    assert isinstance(manager.get_ownership_provider(), PostgresOwnershipProvider)
    assert manager.get_store().ownership_provider is manager.get_ownership_provider()

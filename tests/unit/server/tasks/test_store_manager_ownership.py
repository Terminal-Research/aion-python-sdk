"""Tests that task storage and ownership enforcement are selected together."""

from unittest.mock import Mock, patch

from aion.server.tasks.ownership import DegenerateOwnershipProvider, PostgresOwnershipProvider
from aion.server.tasks.store_manager import StoreManager
from aion.server.tasks.stores.in_memory_task_store import InMemoryTaskStore
from aion.server.tasks.stores.postgres_task_store import PostgresTaskStore


def test_uninitialized_database_selects_in_memory_pair() -> None:
    """The development store is always paired with the degenerate provider.

    The provider's two switches are asserted, not just its type: they are the
    non-goal itself. Without a database there is no lease to enforce and no
    reconciler to reclaim one, so this build offers neither cross-process
    ownership nor recovery from a hard crash - by construction, and not as a
    gap someone forgot to fill.
    """
    manager = StoreManager()

    with patch("aion.server.tasks.store_manager.db_manager", Mock(is_initialized=False)):
        manager.initialize("test-agent")

    provider = manager.get_ownership_provider()

    assert isinstance(manager.get_store(), InMemoryTaskStore)
    assert isinstance(provider, DegenerateOwnershipProvider)
    assert manager.get_store().ownership_provider is provider
    assert provider.enforcement_enabled is False
    assert provider.reconciler_enabled is False


def test_initialized_database_selects_postgres_pair() -> None:
    """The production store is always paired with the fenced provider."""
    manager = StoreManager()

    with patch("aion.server.tasks.store_manager.db_manager", Mock(is_initialized=True)):
        manager.initialize("test-agent")

    assert isinstance(manager.get_store(), PostgresTaskStore)
    assert isinstance(manager.get_ownership_provider(), PostgresOwnershipProvider)
    assert manager.get_store().ownership_provider is manager.get_ownership_provider()

"""Module-level task store manager that holds the active store instance for the server process."""

import logging
from typing import Optional


from aion.db.postgres import db_manager
from .ownership import OwnershipProvider, PostgresOwnershipProvider
from .stores import (
    BaseTaskStore,
    InMemoryTaskStore,
    PostgresTaskStore
)

logger = logging.getLogger(__name__)


class StoreManager:
    """
    Singleton manager for task store implementations.

    Automatically selects the appropriate storage backend based on database
    manager initialization state. Provides PostgreSQL storage when available,
    falls back to in-memory storage otherwise.
    """

    def __init__(self):
        self._is_initialized = False
        self._store: Optional[InMemoryTaskStore | PostgresTaskStore] = None
        self._ownership_provider: Optional[OwnershipProvider] = None

    def initialize(self, agent_id: str):
        """
        Initialize the store manager with appropriate storage backend.

        Selects PostgresTaskStore if database manager is initialized,
        otherwise uses InMemoryTaskStore as fallback. Safe to call multiple times.

        Args:
            agent_id: Identity of the agent this process serves. Scopes every
                task and claim the Postgres backend touches, so several
                agents can share one database. Unused by the in-memory
                fallback, which is already isolated by being unshared.
        """
        if self._is_initialized:
            logger.warning("Tried to initialize store, already initialized")
            return

        if db_manager.is_initialized:
            ownership_provider = PostgresOwnershipProvider(agent_id)
            task_store = PostgresTaskStore(agent_id=agent_id, ownership_provider=ownership_provider)
        else:
            task_store = InMemoryTaskStore()
            ownership_provider = task_store.ownership_provider
            logger.warning(
                "Task ownership enforcement is disabled; in-memory task storage "
                "is safe only in a single server instance"
            )

        self._is_initialized = True
        self._store = task_store
        self._ownership_provider = ownership_provider

    def get_store(self) -> BaseTaskStore:
        """
        Get the initialized task store instance.

        Returns:
           BaseTaskStore: The active task store implementation

        Raises:
           RuntimeError: If called before initialization
        """
        if not self._is_initialized:
            raise RuntimeError("Trying to get a store without initialization")
        return self._store

    def get_ownership_provider(self) -> OwnershipProvider:
        """Return the provider selected alongside the active task store.

        Storage and enforcement are one decision: a shared store always comes
        with fenced claims, a process-local store always comes with the
        degenerate provider, and no third combination can be assembled.
        """
        if not self._is_initialized:
            raise RuntimeError("Trying to get ownership provider without initialization")
        return self._ownership_provider

store_manager = StoreManager()

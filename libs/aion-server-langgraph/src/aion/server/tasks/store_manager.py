from typing import Optional

from aion.shared.logging import get_logger

from aion.server.db import db_manager
from .stores import (
    BaseTaskStore,
    InMemoryTaskStore,
    PostgresTaskStore
)

logger = get_logger(__name__)


class StoreManager:
    """
    Singleton manager for task store implementations.

    Automatically selects the appropriate storage backend based on database
    manager initialization state. Provides PostgreSQL storage when available,
    falls back to in-memory storage otherwise.
    """

    def __init__(self):
        self._is_initialized = False
        self._store: Optional[InMemoryTaskStore | PostgresTaskStore]  = None

    def initialize(self):
        """
        Initialize the store manager with appropriate storage backend.

        Selects PostgresTaskStore if database manager is initialized,
        otherwise uses InMemoryTaskStore as fallback. Safe to call multiple times.
        """
        if self._is_initialized:
            logger.warning("Tried to initialize store, already initialized")
            return

        if db_manager.is_initialized:
            task_store = PostgresTaskStore()
        else:
            task_store = InMemoryTaskStore()

        self._is_initialized = True
        self._store = task_store

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

store_manager = StoreManager()

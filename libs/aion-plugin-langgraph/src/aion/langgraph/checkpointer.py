"""LangGraph-specific checkpoint management.

This module provides checkpoint-related classes and the LangGraphCheckpointerAdapter
for creating checkpointer instances for different storage backends.

Note: Most checkpoint operations (save, load, list, delete) are handled automatically
by LangGraph. The adapter only provides factory methods for creating and validating
checkpointer instances.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from aion.shared.db import DbManagerProtocol
from aion.shared.logging import get_logger
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

logger = get_logger()


class CheckpointerType(str, Enum):
    """Enumeration of supported checkpoint storage types."""
    MEMORY = "memory"
    POSTGRES = "postgres"


@dataclass
class CheckpointerConfig:
    """Configuration for checkpoint storage backend.

    Attributes:
        type: The type of storage backend to use
        connection_string: Connection string for remote storage backends
        namespace: Optional namespace/prefix for checkpoint isolation
        ttl: Time-to-live in seconds for automatic cleanup
        metadata: Additional backend-specific configuration
    """
    type: CheckpointerType
    connection_string: Optional[str] = None
    namespace: Optional[str] = None
    ttl: Optional[int] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Checkpoint:
    """A saved checkpoint of agent execution state.

    Attributes:
        id: Unique identifier for this checkpoint
        thread_id: Thread identifier linking related checkpoints
        state: The saved state data
        timestamp: When the checkpoint was created
        parent_id: Optional ID of the previous checkpoint (for history tracking)
        metadata: Additional checkpoint metadata
    """

    id: str
    thread_id: str
    state: dict[str, Any]
    timestamp: float
    parent_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


class CheckpointerAdapter(ABC):
    """Abstract base for LangGraph checkpoint management.

    Subclasses must implement factory methods for creating checkpointer instances.
    The actual checkpoint operations (save, load, etc.) are handled by
    LangGraph's native checkpointer implementation.

    The CheckpointerAdapter is responsible for:
    - Creating backend-specific checkpointer instances
    - Validating backend connections
    """

    @abstractmethod
    async def create_checkpointer(self, config: CheckpointerConfig) -> Any:
        """Create a backend-specific checkpointer instance.

        Args:
            config: Checkpoint configuration specifying storage backend

        Returns:
            Any: A checkpointer instance ready for use

        Raises:
            ValueError: If configuration is invalid or connection fails
        """
        pass

    async def validate_connection(self, checkpointer: Any) -> bool:
        """Validate that checkpointer can connect to backend.

        Args:
            checkpointer: The checkpointer instance to validate

        Returns:
            bool: True if connection is valid, False otherwise
        """
        return True


class LangGraphCheckpointerAdapter(CheckpointerAdapter):
    """LangGraph checkpointer adapter with database dependency injection.

    This adapter creates checkpointer instances for different storage backends
    (in-memory, PostgreSQL) based on configuration. For PostgreSQL checkpointers,
    a database manager instance must be provided via dependency injection.
    """

    def __init__(self, db_manager: Optional[DbManagerProtocol] = None):
        """Initialize adapter with optional database manager.

        Args:
            db_manager: Database manager instance for PostgreSQL checkpointer support.
                       If None, PostgreSQL checkpointers will fall back to InMemorySaver.
        """
        self._db_manager = db_manager

    async def create_checkpointer(
            self,
            config: CheckpointerConfig,
    ) -> Any:
        logger.debug(f"Creating checkpointer of type: {config.type}")

        if config.type == CheckpointerType.MEMORY:
            return self._create_memory_checkpointer()

        elif config.type == CheckpointerType.POSTGRES:
            if not self._db_manager:
                logger.warning(
                    "Database manager not provided, falling back to InMemorySaver"
                )
                return self._create_memory_checkpointer()

            postgres_checkpointer = await self._create_postgres_checkpointer()
            if postgres_checkpointer is None:
                logger.warning(
                    "PostgreSQL checkpointer creation failed, falling back to InMemorySaver"
                )
                return self._create_memory_checkpointer()
            return postgres_checkpointer

        else:
            raise ValueError(
                f"Unsupported checkpointer type: {config.type}. "
                f"Supported types: {CheckpointerType.MEMORY}, {CheckpointerType.POSTGRES}"
            )

    async def validate_connection(self, checkpointer: Any) -> bool:
        if isinstance(checkpointer, InMemorySaver):
            return True

        elif isinstance(checkpointer, AsyncPostgresSaver):
            try:
                if not self._db_manager:
                    return False
                return self._db_manager.is_initialized
            except Exception as e:
                logger.error(f"Failed to validate PostgreSQL checkpointer: {e}")
                return False

        return True

    @staticmethod
    def _create_memory_checkpointer() -> InMemorySaver:
        """Create in-memory checkpointer.

        Returns:
            InMemorySaver instance
        """
        return InMemorySaver()

    async def _create_postgres_checkpointer(self) -> Optional[AsyncPostgresSaver]:
        """Create PostgreSQL checkpointer using injected database manager.

        Returns:
            AsyncPostgresSaver instance or None if creation fails
        """
        try:
            if not self._db_manager or not self._db_manager.is_initialized:
                logger.warning("Database manager not initialized")
                return None

            pool = self._db_manager.get_pool()
            if not pool:
                logger.warning("Database pool not available")
                return None


            checkpointer = AsyncPostgresSaver(conn=pool)

            # Setup checkpointer (creates database tables if they don't exist)
            logger.debug("Setting up PostgreSQL checkpointer tables")
            await checkpointer.setup()

            logger.info("Created AsyncPostgresSaver checkpointer (setup completed)")
            return checkpointer

        except Exception as ex:
            logger.error(f"Failed to create PostgreSQL checkpointer: {ex}")
            return None

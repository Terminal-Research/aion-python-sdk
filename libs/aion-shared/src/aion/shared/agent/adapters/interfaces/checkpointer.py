"""Abstract base class for checkpoint management adapters.

This module provides checkpoint-related classes and the CheckpointerAdapter interface
for creating checkpointer instances for different storage backends.

Note: Most checkpoint operations (save, load, list, delete) are handled automatically
by the frameworks themselves (e.g., LangGraph). The adapter only provides factory
methods for creating and validating checkpointer instances.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


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
    """Abstract base for framework-specific checkpoint management.

    Subclasses must implement factory methods for creating checkpointer instances.
    The actual checkpoint operations (save, load, etc.) are handled by the
    framework's native checkpointer implementation.

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


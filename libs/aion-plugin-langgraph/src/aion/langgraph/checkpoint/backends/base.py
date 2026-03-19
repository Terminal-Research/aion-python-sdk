"""Base interface for checkpointer backends."""

from abc import ABC, abstractmethod
from typing import Any


class CheckpointerBackend(ABC):
    """Abstract base for checkpointer backends.

    Subclasses must implement is_available and create. The optional setup
    method handles one-time storage initialization (e.g., creating DB tables).
    """

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this backend is available for use."""
        pass

    @abstractmethod
    async def create(self) -> Any:
        """Create and return a checkpointer instance."""
        pass


__all__ = ["CheckpointerBackend"]

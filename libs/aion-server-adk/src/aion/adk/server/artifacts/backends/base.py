"""Base interface for artifact service backends.

This module defines the abstract base for different artifact storage backends.
All artifact service backends must inherit from ArtifactServiceBackend.
"""

from abc import ABC, abstractmethod
from typing import Any


class ArtifactServiceBackend(ABC):
    """Abstract base for artifact service backends.

    Subclasses must implement the create method to return a concrete
    artifact service implementation (e.g., InMemoryArtifactService).
    """

    @abstractmethod
    def create(self) -> Any:
        """Create and return an artifact service instance.

        Returns:
            Any: An artifact service instance ready for use

        Raises:
            Exception: If artifact service creation fails
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this backend is available for use.

        Returns:
            bool: True if backend can be used, False otherwise
        """
        pass


__all__ = ["ArtifactServiceBackend"]

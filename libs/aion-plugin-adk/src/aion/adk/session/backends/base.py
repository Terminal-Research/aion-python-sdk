"""Base interface for session service backends.

This module defines the abstract base for different session storage backends.
All session service backends must inherit from SessionServiceBackend.
"""

from abc import ABC, abstractmethod
from typing import Any


class SessionServiceBackend(ABC):
    """Abstract base for session service backends.

    Subclasses must implement the create method to return a concrete
    session service implementation (e.g., InMemorySessionService, DatabaseSessionService).
    """

    @abstractmethod
    def create(self) -> Any:
        """Create and return a session service instance.

        Returns:
            Any: A session service instance ready for use

        Raises:
            Exception: If session service creation fails
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this backend is available for use.

        Returns:
            bool: True if backend can be used, False otherwise
        """
        pass


__all__ = ["SessionServiceBackend"]

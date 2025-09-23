from abc import ABC, abstractmethod

__all__ = [
    "IAuthManager",
]


class IAuthManager(ABC):
    """Abstract interface for authentication token management."""

    @abstractmethod
    async def get_token(self) -> str:
        """Get authentication token."""
        ...

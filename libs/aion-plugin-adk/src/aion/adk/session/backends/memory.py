"""In-memory session service backend.

This module provides a memory-based session storage backend using
Google ADK's InMemorySessionService.
"""

from typing import Any

from aion.shared.logging import get_logger
from google.adk.sessions import InMemorySessionService

from .base import SessionServiceBackend

logger = get_logger()


class MemoryBackend(SessionServiceBackend):
    """In-memory session service backend.

    This backend uses ADK's InMemorySessionService for session storage.
    Sessions are stored in memory and will be lost when the application restarts.

    Use this backend for:
    - Development and testing
    - Stateless applications
    - When database is not available
    """

    def create(self) -> InMemorySessionService:
        """Create in-memory session service instance.

        Returns:
            InMemorySessionService: ADK's in-memory session service
        """
        return InMemorySessionService()

    def is_available(self) -> bool:
        """Check if memory backend is available.

        Returns:
            bool: Always returns True as memory is always available
        """
        return True


__all__ = ["MemoryBackend"]

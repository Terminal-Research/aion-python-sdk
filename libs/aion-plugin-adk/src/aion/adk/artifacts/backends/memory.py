"""In-memory artifact service backend.

This module provides a memory-based artifact storage backend using
Google ADK's InMemoryArtifactService.
"""

from aion.shared.logging import get_logger
from google.adk.artifacts import InMemoryArtifactService

from .base import ArtifactServiceBackend

logger = get_logger()


class MemoryBackend(ArtifactServiceBackend):
    """In-memory artifact service backend.

    This backend uses ADK's InMemoryArtifactService for artifact storage.
    Artifacts are stored in memory and will be lost when the application restarts.

    Use this backend for:
    - Development and testing
    - Stateless applications
    - When persistent artifact storage is not required
    """

    def create(self) -> InMemoryArtifactService:
        """Create in-memory artifact service instance.

        Returns:
            InMemoryArtifactService: ADK's in-memory artifact service
        """
        return InMemoryArtifactService()

    def is_available(self) -> bool:
        """Check if memory backend is available.

        Returns:
            bool: Always returns True as memory is always available
        """
        return True


__all__ = ["MemoryBackend"]

"""Artifact service factory for ADK plugin.

This module provides the ArtifactServiceFactory for creating artifact service
instances by selecting the appropriate storage backend.
"""

from typing import Optional

from aion.shared.db import DbManagerProtocol
from aion.shared.files.storage import FileUploadManager
from aion.shared.logging import get_logger
from google.adk.artifacts import BaseArtifactService

from .backends import A2ABackend, MemoryBackend

logger = get_logger()


class ArtifactServiceFactory:
    """Factory for creating ADK artifact service instances.

    When a db_manager is provided, returns A2AArtifactService with DB fallback
    and TTL memory eviction. Otherwise falls back to plain InMemoryArtifactService.
    """

    @classmethod
    def create(
        cls,
        db_manager: Optional[DbManagerProtocol] = None,
        file_uploader: Optional[FileUploadManager] = None,
    ) -> BaseArtifactService:
        """Create artifact service using the most appropriate backend.

        Args:
            db_manager: Optional database manager. When provided and initialized,
                        creates A2AArtifactService with DB fallback and TTL eviction.
            file_uploader: Optional upload manager. When provided, inline artifact
                           data is uploaded and stored as URI references.

        Returns:
            BaseArtifactService: An artifact service instance
        """
        service = None
        if db_manager:
            backend = A2ABackend(db_manager=db_manager, file_uploader=file_uploader)
            service = backend.create()

        if not service:
            service = MemoryBackend().create()

        logger.info(f"Initialized {type(service).__name__}")
        return service


__all__ = ["ArtifactServiceFactory"]

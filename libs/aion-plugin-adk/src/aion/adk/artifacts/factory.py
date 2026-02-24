"""Artifact service factory for ADK plugin.

This module provides the ArtifactServiceFactory for creating artifact service
instances by selecting the appropriate storage backend.
"""

from aion.shared.logging import get_logger
from google.adk.artifacts import BaseArtifactService

from .backends import MemoryBackend

logger = get_logger()


class ArtifactServiceFactory:
    """Factory for creating ADK artifact service instances.

    Selects the appropriate backend and returns a ready-to-use artifact service.
    Currently supports in-memory storage with extensibility for custom backends.
    """

    @classmethod
    def create(cls) -> BaseArtifactService:
        """Create artifact service using the most appropriate backend.

        Returns:
            BaseArtifactService: An artifact service instance
        """
        service = cls._create_memory()
        logger.info(f"Initialized {type(service).__name__}")
        return service

    @staticmethod
    def _create_memory() -> BaseArtifactService:
        """Create an in-memory artifact service as the default backend."""
        return MemoryBackend().create()


__all__ = ["ArtifactServiceFactory"]

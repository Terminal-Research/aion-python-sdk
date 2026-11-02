"""Artifact service factory for ADK server package.

This module provides the ArtifactServiceFactory for creating artifact service
instances by selecting the appropriate storage backend.
"""

import logging
from typing import Optional

from aion.core.db import DbManagerProtocol
from google.adk.artifacts import BaseArtifactService

from .backends import A2ABackend

logger = logging.getLogger(__name__)


class ArtifactServiceFactory:
    """Factory for creating ADK artifact service instances.

    Always an A2AArtifactService: it reads the tasks' artifacts by name - through
    the database when there is one, through each turn's conversation history
    otherwise - so ADK's own tools find what the agent published to the task.
    """

    @classmethod
    def create(
        cls,
        db_manager: Optional[DbManagerProtocol] = None,
    ) -> BaseArtifactService:
        """Create artifact service using the most appropriate backend.

        Args:
            db_manager: Optional database manager, for the service's database
                        reads and version records.

        Returns:
            BaseArtifactService: An artifact service instance
        """
        service = A2ABackend(db_manager=db_manager).create()

        logger.info(f"Initialized {type(service).__name__}")
        return service


__all__ = ["ArtifactServiceFactory"]

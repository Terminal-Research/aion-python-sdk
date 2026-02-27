"""Artifact management module for ADK plugin.

This module handles artifact creation and lifecycle management
with support for multiple storage backends (memory, and custom backends).
"""

from .backends import A2ABackend, A2AArtifactService
from .factory import ArtifactServiceFactory

__all__ = ["A2ABackend", "A2AArtifactService", "ArtifactServiceFactory"]

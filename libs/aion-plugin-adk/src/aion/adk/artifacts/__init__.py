"""Artifact management module for ADK plugin.

This module handles artifact creation and lifecycle management
with support for multiple storage backends (memory, and custom backends).
"""

from .factory import ArtifactServiceFactory

__all__ = ["ArtifactServiceFactory"]

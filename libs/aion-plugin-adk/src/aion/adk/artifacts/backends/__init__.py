"""Artifact service backends for ADK plugin.

This module provides different storage backends for ADK artifacts:
- MemoryBackend: In-memory artifact storage (non-persistent)

New backends can be added by implementing the ArtifactServiceBackend interface.
"""

from .base import ArtifactServiceBackend
from .memory import MemoryBackend

__all__ = [
    "ArtifactServiceBackend",
    "MemoryBackend",
]

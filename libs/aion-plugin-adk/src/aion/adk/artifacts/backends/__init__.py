"""Artifact service backends for ADK plugin.

This module provides different storage backends for ADK artifacts:
- MemoryBackend: In-memory artifact storage (non-persistent)
- A2ABackend: In-memory storage with DB fallback and TTL eviction

New backends can be added by implementing the ArtifactServiceBackend interface.
"""

from .a2a import A2ABackend, A2AArtifactService
from .base import ArtifactServiceBackend
from .memory import MemoryBackend

__all__ = [
    "A2ABackend",
    "A2AArtifactService",
    "ArtifactServiceBackend",
    "MemoryBackend",
]

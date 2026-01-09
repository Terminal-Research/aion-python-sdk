"""Session service backends for ADK plugin.

This module provides different storage backends for ADK sessions:
- MemoryBackend: In-memory session storage (non-persistent)
- DatabaseBackend: PostgreSQL-backed session storage (persistent)

New backends can be added by implementing the SessionServiceBackend interface.
"""

from .base import SessionServiceBackend, SessionServiceBackendFactory
from .database import DatabaseBackend
from .memory import MemoryBackend

__all__ = [
    "SessionServiceBackend",
    "SessionServiceBackendFactory",
    "MemoryBackend",
    "DatabaseBackend",
]

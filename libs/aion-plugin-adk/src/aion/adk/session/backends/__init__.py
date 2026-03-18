"""Session service backends for ADK plugin.

This module provides different storage backends for ADK sessions:
- MemoryBackend: In-memory session storage (non-persistent)
- PostgresBackend: PostgreSQL-backed session storage (persistent)

New backends can be added by implementing the SessionServiceBackend interface.
"""

from .base import SessionServiceBackend
from .postgres import PostgresBackend
from .memory import MemoryBackend

__all__ = [
    "SessionServiceBackend",
    "MemoryBackend",
    "PostgresBackend",
]

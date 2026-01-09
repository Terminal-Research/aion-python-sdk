"""Session management module for ADK plugin.

This module handles session creation, storage, and lifecycle management
with support for multiple storage backends (memory, database).
"""

from .manager import SessionServiceManager

__all__ = ["SessionServiceManager"]

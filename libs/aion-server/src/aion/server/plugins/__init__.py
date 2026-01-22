"""Server-side plugin initialization and lifecycle management.

This module provides plugin orchestration, lifecycle management, and integration
with the server's systems. It builds on top of the shared plugin protocols and
registry to provide full plugin lifecycle support.
"""

from .factory import PluginFactory

__all__ = [
    "PluginFactory",
]

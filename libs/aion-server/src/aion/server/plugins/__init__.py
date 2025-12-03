"""Server-side plugin management with business logic.

This module provides plugin orchestration, lifecycle management, and integration
with the server's systems. It builds on top of the shared plugin protocols and
registry to provide full plugin lifecycle support.
"""

from .manager import PluginManager, plugin_manager

__all__ = [
    "PluginManager",
    "plugin_manager",
]

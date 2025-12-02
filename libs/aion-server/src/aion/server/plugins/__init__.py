"""Server-side plugin management with business logic.

This module provides plugin orchestration, lifecycle management, and integration
with the server's systems. It builds on top of the shared plugin protocols and
registry to provide full plugin lifecycle support.

Example:
    from aion.server.plugins import plugin_manager
    from aion.server.db import db_manager

    # At server startup
    await plugin_manager.discover_and_register()
    await plugin_manager.setup_all(db_manager=db_manager)

    # At server shutdown
    await plugin_manager.teardown_all()
"""

from .manager import PluginManager, plugin_manager

__all__ = [
    "PluginManager",
    "plugin_manager",
]

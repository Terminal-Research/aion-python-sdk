"""Plugin system for AION framework integrations.

This module provides the plugin architecture for extending AION with different
frameworks (agents, databases, storage, etc.). Plugins manage infrastructure
concerns (setup, teardown, migrations) while adapters handle runtime behavior.

Architecture:
- BasePluginProtocol: Core interface for all plugins
- AgentPluginProtocol: Specialized interface for agent framework plugins
- PluginRegistry: Simple storage for plugin instances

The server's PluginManager provides orchestration and business logic on top
of these primitives.
"""

from .agent import AgentPluginProtocol
from .base import BasePluginProtocol
from .registry import PluginRegistry, plugin_registry

__all__ = [
    # Protocols
    "BasePluginProtocol",
    "AgentPluginProtocol",
    # Registry
    "PluginRegistry",
    "plugin_registry",
]

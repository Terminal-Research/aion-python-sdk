"""Simple plugin registry for storage and retrieval.

This module provides a lightweight registry for storing and accessing plugins.
It does NOT contain business logic - that belongs in the server's PluginManager.
"""

from typing import Optional, Type, TypeVar

from .base import BasePluginProtocol

T = TypeVar('T', bound=BasePluginProtocol)


class PluginRegistry:
    """Simple plugin storage registry.

    This registry provides basic storage and retrieval operations for plugins.
    It is intentionally lightweight - complex orchestration, lifecycle management,
    and business logic should be handled by the server's PluginManager.

    The registry:
    - Stores plugins by name
    - Provides basic lookup operations
    - Supports type-based filtering
    """

    def __init__(self):
        """Initialize an empty plugin registry."""
        self._plugins: dict[str, BasePluginProtocol] = {}

    def register(self, plugin: BasePluginProtocol) -> None:
        """Register a plugin in the registry.

        Args:
            plugin: Plugin instance to register

        Raises:
            ValueError: If a plugin with the same name is already registered
            TypeError: If plugin doesn't implement BasePluginProtocol
        """
        if not isinstance(plugin, BasePluginProtocol):
            raise TypeError(
                f"Plugin must implement BasePluginProtocol, got {type(plugin).__name__}"
            )

        name = plugin.name()

        if name in self._plugins:
            raise ValueError(
                f"Plugin '{name}' is already registered. "
                f"Existing: {self._plugins[name]}, New: {plugin}"
            )

        self._plugins[name] = plugin

    def unregister(self, name: str) -> Optional[BasePluginProtocol]:
        """Remove a plugin from the registry.

        Args:
            name: Name of the plugin to remove

        Returns:
            Optional[BasePluginProtocol]: The removed plugin, or None if not found
        """
        return self._plugins.pop(name, None)

    def get(self, name: str) -> Optional[BasePluginProtocol]:
        """Get a plugin by name.

        Args:
            name: Plugin name to lookup

        Returns:
            Optional[BasePluginProtocol]: The plugin instance, or None if not found
        """
        return self._plugins.get(name)

    def get_all(self) -> list[BasePluginProtocol]:
        """Get all registered plugins.

        Returns:
            list[BasePluginProtocol]: List of all registered plugin instances
        """
        return list(self._plugins.values())

    def get_by_type(self, plugin_type: Type[T]) -> list[T]:
        """Get all plugins of a specific type.

        Args:
            plugin_type: The plugin protocol type to filter by

        Returns:
            list[T]: List of plugins matching the specified type
        """
        return [
            plugin for plugin in self._plugins.values()
            if isinstance(plugin, plugin_type)
        ]

    def has(self, name: str) -> bool:
        """Check if a plugin is registered.

        Args:
            name: Plugin name to check

        Returns:
            bool: True if plugin is registered, False otherwise
        """
        return name in self._plugins

    def clear(self) -> None:
        """Remove all plugins from the registry.

        Warning: This does not call teardown on plugins. Use PluginManager
        for proper lifecycle management.
        """
        self._plugins.clear()

    def list_names(self) -> list[str]:
        """Get list of all registered plugin names.

        Returns:
            list[str]: List of plugin names
        """
        return list(self._plugins.keys())

    def __contains__(self, name: str) -> bool:
        """Check if a plugin is registered using 'in' operator.

        Args:
            name: Plugin name to check

        Returns:
            bool: True if plugin is registered
        """
        return name in self._plugins

    def __len__(self) -> int:
        """Get the number of registered plugins using len().

        Returns:
            int: Number of registered plugins
        """
        return len(self._plugins)

    def __repr__(self) -> str:
        """String representation of the registry."""
        return f"PluginRegistry(plugins={len(self._plugins)}, names={self.list_names()})"


# Global singleton instance
plugin_registry = PluginRegistry()

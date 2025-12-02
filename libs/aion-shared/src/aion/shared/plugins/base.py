"""Base plugin protocol for all plugin types.

This module defines the foundational interface that all plugins must implement,
providing a consistent contract for plugin lifecycle management across the system.
"""

from abc import ABC, abstractmethod
from typing import Any


class BasePluginProtocol(ABC):
    """Abstract base protocol for all plugin types.

    This protocol defines the core lifecycle methods and metadata that every
    plugin must provide, regardless of its specific functionality (agent, database,
    storage, etc.).

    The plugin architecture separates concerns:
    - Plugin: Manages infrastructure (setup, teardown, migrations)
    - Adapter: Handles runtime behavior (execution, state management)
    """

    @abstractmethod
    def name(self) -> str:
        """Get the unique identifier for this plugin.

        The name should be unique across all plugins, use lowercase with hyphens.

        Returns:
            str: Plugin identifier
        """
        pass

    @abstractmethod
    async def setup(self, **deps: Any) -> None:
        """Initialize the plugin with required dependencies.

        Called once during application startup. Use this to store dependencies,
        run migrations, initialize components, and setup infrastructure.

        Args:
            **deps: Plugin dependencies (db_manager, config, etc.)

        Raises:
            Exception: If setup fails critically
        """
        pass

    async def teardown(self) -> None:
        """Cleanup plugin resources during shutdown.

        Called during application shutdown. Override to close connections,
        flush buffers, and release resources. Should be idempotent and handle
        errors gracefully.
        """
        pass

    async def health_check(self) -> bool:
        """Perform a health check on the plugin.

        Called after setup to verify the plugin is functioning correctly.

        Returns:
            bool: True if plugin is healthy, False otherwise
        """
        return True

    def __repr__(self) -> str:
        """String representation of the plugin."""
        return f"{self.__class__.__name__}(name={self.name()!r})"

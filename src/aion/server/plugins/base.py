"""Base plugin protocol for all plugin types.

This module defines the foundational interface that all plugins must implement,
providing a consistent contract for plugin lifecycle management across the system.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from aion.core.db import DbManagerProtocol
    from aion.db.postgres.migrations import SchemaCheck


class BasePluginProtocol(ABC):
    """Abstract base protocol for all plugin types.

    This protocol defines the core lifecycle methods and metadata that every
    plugin must provide, regardless of its specific functionality (agent, database,
    storage, etc.).

    The plugin architecture separates concerns:
    - Plugin: Manages infrastructure (initialize, teardown, migrations)
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
    async def initialize(
            self,
            db_manager: DbManagerProtocol,
            **deps: Any
    ) -> None:
        """Initialize the plugin with required dependencies.

        Called once during application startup. Use this to store dependencies,
        run migrations, initialize components, and setup infrastructure.

        Args:
            db_manager: Infrastructure database manager
            **deps: Plugin dependencies (db_manager, config, etc.)

        Raises:
            Exception: If initialization fails critically
        """
        pass

    async def migrate_database(self, db_manager: DbManagerProtocol) -> None:
        """Create or upgrade the tables this plugin keeps in the database.

        Called by ``aion.server.database.migrate_database`` - by
        ``aion db migrate``, or by a server starting with
        ``DB_MIGRATE_ON_START`` - after the SDK's own migrations, and only when
        :meth:`check_database` does not report the tables newer than this
        code. Runs no agent code: the plugin is only imported and
        constructed. Must be safe when several servers run it at once. The
        default keeps no tables.

        Args:
            db_manager: An initialized database manager.
        """
        return None

    async def check_database(self, db_manager: DbManagerProtocol) -> Optional[SchemaCheck]:
        """Report whether this plugin's tables are migrated, without changing them.

        Args:
            db_manager: An initialized database manager.

        Returns:
            The state of the plugin's tables, or ``None`` when it keeps none.
        """
        return None

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

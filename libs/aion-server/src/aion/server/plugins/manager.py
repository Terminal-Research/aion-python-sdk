"""Plugin manager with orchestration and business logic.

This module provides the PluginManager which handles all plugin lifecycle
operations, dependency injection, error handling, and integration with the
server's systems.
"""

from typing import Optional

from aion.shared.agent import adapter_registry
from aion.shared.db import DbManagerProtocol
from aion.shared.logging import get_logger, AionLogger
from aion.shared.plugins import (
    AgentPluginProtocol,
    BasePluginProtocol,
    PluginRegistry,
    plugin_registry,
)


class PluginManager:
    """Server-side plugin orchestration with business logic.

    The PluginManager handles:
    - Plugin discovery (finding available plugins)
    - Registration (adding plugins to the registry)
    - Lifecycle management (setup/teardown with dependencies)
    - Integration (connecting plugins with server systems)
    - Error handling and logging

    The PluginManager uses the shared PluginRegistry for storage but adds
    all the complex orchestration logic on top.
    """

    def __init__(self, registry: Optional[PluginRegistry] = None):
        """Initialize the plugin manager.

        Args:
            registry: Plugin registry to use (defaults to global plugin_registry)
        """
        self._registry = registry or plugin_registry
        self._initialized = False
        self._logger: Optional[AionLogger] = None

    @property
    def logger(self) -> AionLogger:
        if not self._logger:
            self._logger = get_logger()
        return self._logger

    async def discover_and_register(self) -> None:
        """Discover available plugins and register them.

        This method attempts to import and instantiate known plugins.
        Plugins that are not installed or fail to import are skipped gracefully.
        """
        self.logger.info("Discovering available plugins...")

        plugins = await self._discover_plugins()

        for plugin in plugins:
            try:
                self.logger.info(f"Registering plugin: {plugin.name()}")
                self._registry.register(plugin)
            except Exception as e:
                self.logger.error(f"Failed to register plugin {plugin.name()}: {e}", exc_info=True)

        self.logger.info(
            f"Plugin discovery complete. Registered {len(plugins)} plugin(s): "
            f"{', '.join(p.name() for p in plugins)}"
        )

    async def setup_all(
            self,
            db_manager: Optional[DbManagerProtocol] = None,
            **extra_deps
    ) -> None:
        """Setup all registered plugins with dependencies.

        This method:
        1. Calls setup() on each plugin with injected dependencies
        2. Performs plugin-specific post-setup (e.g., registering adapters)
        3. Runs health checks
        4. Handles errors gracefully

        Args:
            db_manager: Database manager for plugins that need DB access
            **extra_deps: Additional dependencies to pass to plugins
        """
        if self._initialized:
            self.logger.warning("Plugins already initialized, skipping setup")
            return

        plugins = self._registry.get_all()
        self.logger.info(f"Setting up {len(plugins)} plugin(s)...")

        setup_count = 0
        for plugin in plugins:
            try:
                self.logger.debug(f"Setting up plugin: {plugin.name()}")

                # Call plugin's setup with dependencies
                await plugin.setup(db_manager=db_manager, **extra_deps)

                # Server-specific post-setup integration
                await self._post_setup(plugin, db_manager)

                # Health check
                if await plugin.health_check():
                    self.logger.info(f"Plugin '{plugin.name()}' setup complete and healthy")
                    setup_count += 1
                else:
                    self.logger.warning(f"Plugin '{plugin.name()}' setup complete but health check failed")

            except Exception as e:
                self.logger.error(
                    f"Failed to setup plugin '{plugin.name()}': {e}",
                    exc_info=True
                )

        self._initialized = True
        self.logger.info(f"Plugin setup complete. {setup_count}/{len(plugins)} plugin(s) healthy")

    async def teardown_all(self) -> None:
        """Teardown all plugins in reverse order.

        Calls teardown() on each plugin to cleanup resources.
        Plugins are torn down in reverse registration order.
        """
        if not self._initialized:
            self.logger.debug("Plugins not initialized, skipping teardown")
            return

        plugins = self._registry.get_all()
        self.logger.info(f"Tearing down {len(plugins)} plugin(s)...")

        # Teardown in reverse order
        for plugin in reversed(plugins):
            try:
                self.logger.debug(f"Tearing down plugin: {plugin.name()}")
                await plugin.teardown()
                self.logger.info(f"Plugin '{plugin.name()}' teardown complete")
            except Exception as e:
                self.logger.error(
                    f"Failed to teardown plugin '{plugin.name()}': {e}",
                    exc_info=True
                )

        self._initialized = False
        self.logger.info("Plugin teardown complete")

    async def _discover_plugins(self) -> list[BasePluginProtocol]:
        """Discover available plugins by attempting imports.

        Returns:
            list[BasePluginProtocol]: List of discovered plugin instances
        """
        plugins = []

        # Try to discover LangGraph plugin
        try:
            from aion.langgraph import LangGraphPlugin
            plugin = LangGraphPlugin()
            plugins.append(plugin)
            self.logger.debug(f"Discovered plugin: {plugin.name()}")
        except ImportError:
            self.logger.debug("LangGraph plugin not available (aion-plugin-langgraph not installed)")
        except Exception as e:
            self.logger.warning(f"Failed to load LangGraph plugin: {e}")

        return plugins

    async def _post_setup(
            self,
            plugin: BasePluginProtocol,
            db_manager: Optional[DbManagerProtocol]
    ) -> None:
        """Perform server-specific post-setup integration.

        Args:
            plugin: Plugin that was just setup
            db_manager: Database manager instance
        """
        # If it's an agent plugin, register its adapter
        if isinstance(plugin, AgentPluginProtocol):
            try:
                adapter = plugin.get_adapter()
                framework_name = adapter.framework_name()

                if not adapter_registry.is_registered(framework_name):
                    adapter_registry.register(adapter)
                    self.logger.info(f"Registered adapter for framework: {framework_name}")
                else:
                    self.logger.debug(f"Adapter for {framework_name} already registered")

            except Exception as e:
                self.logger.error(f"Failed to register adapter for {plugin.name()}: {e}")

    def get_registry(self) -> PluginRegistry:
        """Get the underlying plugin registry.

        Returns:
            PluginRegistry: The registry instance used by this manager
        """
        return self._registry

    def is_initialized(self) -> bool:
        """Check if plugins have been initialized.

        Returns:
            bool: True if setup_all has been called, False otherwise
        """
        return self._initialized

    def __repr__(self) -> str:
        """String representation of the plugin manager."""
        return (
            f"PluginManager(initialized={self._initialized}, "
            f"plugins={len(self._registry)})"
        )


# Global singleton instance
plugin_manager = PluginManager()

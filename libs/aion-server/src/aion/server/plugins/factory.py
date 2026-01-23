"""Plugin factory for initialization and lifecycle management.

This module provides the PluginFactory which handles all plugin lifecycle
operations, dependency injection, error handling, and integration with the
server's systems.
"""

from typing import Optional

from aion.shared.agent import AionAgent
from aion.shared.agent.adapters import adapter_registry
from aion.shared.db import DbManagerProtocol
from aion.shared.logging import get_logger, AionLogger
from aion.shared.plugins import (
    AgentPluginProtocol,
    BasePluginProtocol,
    PluginRegistry,
    plugin_registry,
)
from fastapi import FastAPI


class PluginFactory:
    """Factory for plugin initialization and lifecycle management.

    The PluginFactory handles:
    - Plugin discovery (finding available plugins)
    - Registration (adding plugins to the registry)
    - Lifecycle management (setup/teardown with dependencies)
    - Integration (connecting plugins with server systems)
    - Error handling and logging

    The PluginFactory uses the shared PluginRegistry for storage but adds
    all the complex orchestration logic on top.
    """

    def __init__(
            self,
            db_manager: Optional[DbManagerProtocol] = None,
            registry: Optional[PluginRegistry] = None
    ):
        """Initialize the plugin factory.

        Args:
            db_manager: Database manager for plugins that need DB access
            registry: Plugin registry to use (defaults to global plugin_registry)
        """
        self._db_manager = db_manager
        self._registry = registry or plugin_registry
        self._initialized = False
        self._logger: Optional[AionLogger] = None

    @property
    def logger(self) -> AionLogger:
        if not self._logger:
            self._logger = get_logger()
        return self._logger

    async def initialize(self, **extra_deps) -> None:
        """Phase 1: Initialize plugins - discover, register, and setup infrastructure.

        This phase happens BEFORE app configuration and agent building.
        Plugins register adapters and setup infrastructure (DB migrations, etc).

        Args:
            **extra_deps: Additional dependencies to pass to plugins
        """
        # Discover and register plugins
        await self.discover_and_register()

        # Setup all registered plugins with db_manager from constructor
        await self.setup_all(db_manager=self._db_manager, **extra_deps)

    async def discover_and_register(self) -> None:
        """Discover available plugins and register them.

        This method attempts to import and instantiate known plugins.
        Plugins that are not installed or fail to import are skipped gracefully.
        """
        self.logger.debug("Discovering available plugins...")

        plugins = await self._discover_plugins()

        for plugin in plugins:
            try:
                self.logger.debug(f"Registering plugin: {plugin.name()}")
                self._registry.register(plugin)
            except Exception as e:
                self.logger.error(f"Failed to register plugin {plugin.name()}: {e}", exc_info=True)

        self.logger.info(
            f"Registered {len(plugins)} plugin(s): "
            f"{', '.join(p.name() for p in plugins)}"
        )

    async def setup_all(
            self,
            db_manager: Optional[DbManagerProtocol] = None,
            **extra_deps
    ) -> None:
        """Setup all registered plugins with dependencies.

        Args:
            db_manager: Database manager for plugins that need DB access
            **extra_deps: Additional dependencies to pass to plugins
        """
        if self._initialized:
            self.logger.warning("Plugins already initialized, skipping setup")
            return

        plugins = self._registry.get_all()
        self.logger.debug(f"Setting up {len(plugins)} plugin(s)...")

        setup_count = 0
        for plugin in plugins:
            try:
                # Call plugin's initialize with dependencies
                await plugin.initialize(db_manager=db_manager, **extra_deps)

                # Server-specific post-setup integration
                await self._post_setup(plugin)

                # Health check
                if await plugin.health_check():
                    self.logger.debug(f"Plugin '{plugin.name()}' setup complete and healthy")
                    setup_count += 1
                else:
                    self.logger.warning(f"Plugin '{plugin.name()}' setup complete but health check failed")

            except Exception as e:
                self.logger.error(
                    f"Failed to setup plugin '{plugin.name()}': {e}",
                    exc_info=True
                )

        self._initialized = True
        self.logger.info(f"Initialized {setup_count}/{len(plugins)} plugin(s)")

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

    async def _try_load_plugin(
            self,
            module_name: str,
            class_name: str,
            display_name: str
    ) -> Optional[BasePluginProtocol]:
        """Try to load a single plugin by dynamic import.

        Attempts to import and instantiate a plugin class from the specified module.
        If the module is not installed (ImportError), returns None silently.
        For other errors, logs a warning and returns None.

        Args:
            module_name: Full module path (e.g., "aion.langgraph")
            class_name: Plugin class name to import (e.g., "LangGraphPlugin")
            display_name: Human-readable name for logging (e.g., "LangGraph")

        Returns:
            Optional[BasePluginProtocol]: Plugin instance if successful, None otherwise
        """
        try:
            module = __import__(module_name, fromlist=[class_name])
            plugin_class = getattr(module, class_name)
            return plugin_class()

        except ModuleNotFoundError:
            return None

        except ImportError as e:
            self.logger.exception(f"Failed to import plugin '{module_name}': {e}")
            return None

        except Exception as e:
            self.logger.warning(f"Failed to load {display_name} plugin: {e}")
            return None

    async def _discover_plugins(self) -> list[BasePluginProtocol]:
        """Discover available plugins by attempting imports."""
        plugin_configs = [
            ("aion.langgraph", "LangGraphPlugin", "LangGraph"),
            ("aion.adk", "ADKPlugin", "ADK"),
        ]

        plugins = []
        for module_name, class_name, display_name in plugin_configs:
            plugin = await self._try_load_plugin(module_name, class_name, display_name)
            if plugin:
                plugins.append(plugin)

        return plugins

    async def _post_setup(
            self,
            plugin: BasePluginProtocol
    ) -> None:
        """Perform server-specific post-setup integration.

        Args:
            plugin: Plugin that was just setup
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

    async def configure_app(self, app: FastAPI, agent: AionAgent) -> None:
        """Phase 2: Configure FastAPI app with plugin customizations.

        This phase happens AFTER both app build and agent build.
        Plugins can add routes, middlewares, and access the built agent.

        Args:
            app: Built FastAPI application instance
            agent: Built AionAgent instance (has native_agent available)
        """
        plugins = self._registry.get_all()
        self.logger.debug(f"Configuring app with {len(plugins)} plugin(s)...")

        configured_count = 0
        for plugin in plugins:
            # Check if plugin has configure_app method
            if not hasattr(plugin, 'configure_app'):
                continue

            try:
                self.logger.debug(f"Configuring app with plugin: {plugin.name()}")
                await plugin.configure_app(app, agent)
                configured_count += 1
                self.logger.info(f"Plugin '{plugin.name()}' configured app successfully")
            except Exception as e:
                self.logger.error(
                    f"Failed to configure app with plugin '{plugin.name()}': {e}",
                    exc_info=True
                )

        self.logger.info(f"Configured app with {configured_count}/{len(plugins)} plugin(s)")

    def get_registry(self) -> PluginRegistry:
        """Get the underlying plugin registry.

        Returns:
            PluginRegistry: The registry instance used by this factory
        """
        return self._registry

    def is_initialized(self) -> bool:
        """Check if plugins have been initialized.

        Returns:
            bool: True if setup_all has been called, False otherwise
        """
        return self._initialized

    def __repr__(self) -> str:
        """String representation of the plugin factory."""
        return (
            f"PluginFactory(initialized={self._initialized}, "
            f"plugins={len(self._registry)})"
        )


# Note: plugin_factory instance should be created in server.py with db_manager dependency

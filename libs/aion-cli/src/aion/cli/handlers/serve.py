"""Handler for orchestrating AION server startup and management"""
import signal
import sys
from typing import Optional

from aion.cli.services import (
    ServeAgentStartupService,
    ServeMonitoringService,
    ServeProxyStartupService,
    ServeShutdownService,
)
from aion.shared.aion_config import AionConfig
from aion.shared.logging import get_logger
from aion.shared.utils.processes import ProcessManager

logger = get_logger()


class ServeHandler:
    """
    Handler for orchestrating AION agent and proxy server lifecycle.

    This handler coordinates the startup, monitoring, and shutdown of all
    AION agents and proxy server by delegating to specialized services.

    Example:
        handler = ServeHandler()
        successful, failed, proxy_started = await handler.startup(config)
        if successful:
            await handler.monitor()
            await handler.shutdown()
    """

    def __init__(self):
        self.process_manager: Optional[ProcessManager] = None
        self.config: Optional[AionConfig] = None
        self.successful_agents: list[str] = []
        self.failed_agents: list[str] = []
        self.proxy_started: bool = False
        self._setup_signal_handlers()

    def _setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        logger.debug(
            f"Received signal {signum}, shutting down all agents and proxy..."
        )
        if self.process_manager:
            self.process_manager.shutdown_all(timeout=30)
        sys.exit(0)

    async def startup(self, config: AionConfig) -> tuple[list[str], list[str], bool]:
        """
        Start all configured agents and proxy server.

        Args:
            config: AION configuration instance

        Returns:
            tuple: (successful_agents, failed_agents, proxy_started)
        """
        # Store config for later use
        self.config = config

        # Initialize process manager
        self.process_manager = ProcessManager()

        # Start all configured agents
        self.successful_agents, self.failed_agents = await ServeAgentStartupService().execute(
            config, self.process_manager
        )

        # Report agent startup results
        if self.successful_agents:
            logger.info(
                f"Successfully started agents: {', '.join(self.successful_agents)}"
            )

        if self.failed_agents:
            logger.error(f"Failed to start agents: {', '.join(self.failed_agents)}")

        if not self.successful_agents:
            logger.error("No agents started successfully, exiting...")
            return self.successful_agents, self.failed_agents, False

        # Start proxy server if configured
        if config.proxy:
            self.proxy_started = await ServeProxyStartupService().execute(
                config, self.process_manager
            )
            if not self.proxy_started:
                logger.error("Failed to start proxy server")

        return self.successful_agents, self.failed_agents, self.proxy_started

    async def monitor(self) -> None:
        """
        Monitor running processes and handle restarts.

        This is a blocking call that runs until all agents stop or shutdown is requested.
        Uses internal state from startup() call.

        Raises:
            RuntimeError: If called before startup()
        """
        if not self.process_manager or not self.config:
            raise RuntimeError("monitor() called before startup()")

        await ServeMonitoringService().execute(
            successful_agents=self.successful_agents,
            proxy_started=self.proxy_started,
            config=self.config,
            process_manager=self.process_manager,
        )

    async def shutdown(self, timeout: int = 30) -> bool:
        """
        Gracefully shutdown all processes.

        Args:
            timeout: Maximum time in seconds to wait for processes to shutdown

        Returns:
            bool: True if all processes shut down successfully
        """
        if not self.process_manager:
            logger.warning("shutdown() called but no process manager initialized")
            return True

        return await ServeShutdownService().execute(process_manager=self.process_manager, timeout=timeout)

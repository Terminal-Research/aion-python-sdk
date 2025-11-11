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
from aion.cli.utils.cli_messages import welcome_message
from aion.cli.utils.port_manager import AionPortManager
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
        self.port_manager: Optional[AionPortManager] = None
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
        if self.port_manager:
            self.port_manager.release_all()
        sys.exit(0)

    async def startup(
        self,
        config: AionConfig,
        proxy_port: int | None = None,
        port_range_start: int = 8000,
        port_range_end: int = 9000,
        proxy_port_search_start: int = 8000,
        proxy_port_search_end: int = 8100
    ) -> tuple[list[str], list[str], bool]:
        """
        Start all configured agents and proxy server with dynamic port allocation.

        Args:
            config: AION configuration instance
            proxy_port: Optional port for proxy server (if None, will auto-find)
            port_range_start: Starting port of the range for agents
            port_range_end: Ending port of the range for agents
            proxy_port_search_start: Starting port for proxy search if auto-finding
            proxy_port_search_end: Ending port for proxy search if auto-finding

        Returns:
            tuple: (successful_agents, failed_agents, proxy_started)
        """
        # Store config for later use
        self.config = config

        # Initialize port reservation manager
        self.port_manager = AionPortManager()

        if proxy_port is None:
            # Auto-find proxy port
            found_proxy_port = self.port_manager.reserve_proxy_from_range(
                proxy_port_search_start,
                proxy_port_search_end
            )
            if found_proxy_port is None:
                logger.error(f"Failed to auto-find proxy port in range {proxy_port_search_start}-{proxy_port_search_end}")
                self.port_manager.release_all()
                return [], [], False

            proxy_port = found_proxy_port

            # If port range was calculated based on default assumption,
            # recalculate it based on the actual found proxy port
            # Only recalculate if we're using the default range that assumes proxy at 8000
            if port_range_start == 8001 and port_range_end == 9001:
                port_range_start = proxy_port + 1
                port_range_end = port_range_start + 1000
                logger.debug(f"Recalculated port range to {port_range_start}-{port_range_end}")

        elif proxy_port is not None:
            # Reserve explicit proxy port
            if not self.port_manager.reserve_proxy_port(proxy_port):
                logger.error(f"Failed to reserve proxy port {proxy_port}")
                self.port_manager.release_all()
                return [], [], False
            logger.info(f"Reserved proxy port {proxy_port}")

        if not self.port_manager.reserve_agent_ports(
            agent_ids=list(config.agents.keys()),
            port_range_start=port_range_start,
            port_range_end=port_range_end
        ):
            logger.error("Failed to reserve agent ports")
            self.port_manager.release_all()
            return [], [], False

        # Initialize process manager
        self.process_manager = ProcessManager()

        # Start all configured agents with reserved ports
        self.successful_agents, self.failed_agents = await ServeAgentStartupService().execute(
            config=config,
            process_manager=self.process_manager,
            port_manager=self.port_manager
        )

        # Report agent startup results
        if self.failed_agents:
            logger.error(f"Failed to start agents: {', '.join(self.failed_agents)}")

        if not self.successful_agents:
            logger.error("No agents started successfully, exiting...")
            return self.successful_agents, self.failed_agents, False

        # Start proxy server if port was specified
        if proxy_port is not None:
            # Build agents dictionary (agent_id -> agent_url) using reserved ports
            agents = {}
            for agent_id in config.agents.keys():
                agent_port = self.port_manager.get_agent_port(agent_id)
                if agent_port:
                    # Build agent URL using hardcoded host 0.0.0.0 and http scheme
                    agent_url = f"http://0.0.0.0:{agent_port}"
                    agents[agent_id] = agent_url

            self.proxy_started = await ServeProxyStartupService().execute(
                port=proxy_port,
                agents=agents,
                process_manager=self.process_manager,
                port_manager=self.port_manager
            )
            if not self.proxy_started:
                logger.error("Failed to start proxy server")

        # Print welcome message after successful startup
        try:
            print(welcome_message(port_manager=self.port_manager))
        except:
            pass

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
        Gracefully shutdown all processes and release reserved ports.

        Args:
            timeout: Maximum time in seconds to wait for processes to shutdown

        Returns:
            bool: True if all processes shut down successfully
        """
        shutdown_successful = True

        if self.process_manager:
            shutdown_successful = await ServeShutdownService().execute(
                process_manager=self.process_manager,
                timeout=timeout
            )
        else:
            logger.warning("shutdown() called but no process manager initialized")

        # Release all reserved ports
        if self.port_manager:
            try:
                self.port_manager.release_all()
                logger.debug("Released all reserved ports")
            except Exception as e:
                logger.error(f"Error releasing ports: {e}")
                shutdown_successful = False

        return shutdown_successful

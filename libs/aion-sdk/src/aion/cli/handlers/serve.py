"""Handler for orchestrating AION server startup and management"""
import logging
import asyncio
import signal
from contextlib import suppress
from typing import Optional

from aion.api.http import aion_jwt_manager
from aion.core.config import AionConfig
from aion.core.settings import api_settings
from aion.server import services as aion_services
from aion.server.core.platform import AionWebSocketManager, WebsocketTransportFactory
from aion.server.utils.processes import ProcessManager

from aion.cli.services import (
    EnvironmentContext,
    ServeAgentStartupService,
    ServeEnvironmentPreparerService,
    ServeMonitoringService,
    ServeProxyStartupService,
    ServeShutdownService,
    AionDeploymentRegisterVersionService,
)
from aion.cli.utils.cli_messages import generate_welcome_message
from aion.cli.utils.port_manager import AionPortManager

logger = logging.getLogger(__name__)


class ServeHandler:
    """
    Handler for orchestrating AION agent and proxy server lifecycle.

    This handler coordinates the complete lifecycle: startup, config broadcast,
    monitoring, and shutdown of all AION agents and proxy server by delegating
    to specialized services.
    """

    def __init__(self):
        self.process_manager: Optional[ProcessManager] = None
        self.port_manager: Optional[AionPortManager] = None
        self.config: Optional[AionConfig] = None
        self.env_context: Optional[EnvironmentContext] = None
        self.successful_agents: list[str] = []
        self.failed_agents: list[str] = []
        self.proxy_started: bool = False
        self._platform_link_task: Optional[asyncio.Task] = None
        self._websocket_manager: Optional[AionWebSocketManager] = None
        self._shutdown_requested = asyncio.Event()
        self._background_tasks: set[asyncio.Task] = set()

    def _setup_signal_handlers(self) -> None:
        """Route SIGINT/SIGTERM into the running loop as a shutdown request.

        Signal handlers run between bytecodes on the main thread, so doing the
        shutdown *in* one blocks the event loop for as long as the child
        processes take to die (up to 30s), and racing it against the normal
        ``finally`` path would shut everything down twice. Instead the handler
        only sets an event, and ``run``'s own ``finally`` performs the one
        graceful, awaited shutdown.

        Falls back to plain ``signal.signal`` where the loop cannot install
        handlers (Windows), which is no worse than the previous behaviour.
        """
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._request_shutdown, sig)
            except (NotImplementedError, RuntimeError):
                signal.signal(sig, lambda signum, _frame: self._request_shutdown(signum))

    def _request_shutdown(self, signum) -> None:
        """Record that a shutdown signal arrived; the loop does the rest."""
        logger.debug(
            f"Received signal {signum}, shutting down all agents and proxy..."
        )
        self._shutdown_requested.set()

    def _track_background_task(self, task: asyncio.Task, description: str) -> None:
        """Hold a reference to ``task`` and surface whatever it raises.

        A bare ``asyncio.create_task`` result is only weakly referenced by the
        loop: without a strong reference the task can be garbage-collected
        mid-flight, and an exception inside it is never observed by anyone.
        """
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        def _report(finished: asyncio.Task) -> None:
            if finished.cancelled():
                return
            error = finished.exception()
            if error is not None:
                logger.warning(f"{description} failed: {error}")

        task.add_done_callback(_report)

    async def run(
            self,
            config: AionConfig,
            proxy_port: int | None = None,
            port_range_start: int = 8000,
            port_range_end: int = 9000,
            proxy_port_search_start: int = 8000,
            proxy_port_search_end: int = 8100,
            startup_timeout: int = 30
    ) -> None:
        """
        Complete lifecycle: startup, broadcast config, monitor, and shutdown.

        This is the main entry point that orchestrates the entire AION system lifecycle.

        Args:
            config: AION configuration instance
            proxy_port: Optional port for proxy server (if None, will auto-find)
            port_range_start: Starting port of the range for agents
            port_range_end: Ending port of the range for agents
            proxy_port_search_start: Starting port for proxy search if auto-finding
            proxy_port_search_end: Ending port for proxy search if auto-finding
            startup_timeout: Timeout in seconds for startup confirmation (0 to skip)
        """
        try:
            self._setup_signal_handlers()

            # Startup phase
            successful_agents, failed_agents, proxy_started = await self._startup(
                config=config,
                proxy_port=proxy_port,
                port_range_start=port_range_start,
                port_range_end=port_range_end,
                proxy_port_search_start=proxy_port_search_start,
                proxy_port_search_end=proxy_port_search_end,
                startup_timeout=startup_timeout
            )

            # Exit if no agents started successfully
            if not successful_agents:
                return

            # Announce this deployment to the platform, in the background so the
            # agents keep serving while registration retries. Tracked rather than
            # left to the loop, which holds only a weak reference: an unreferenced
            # task can be collected mid-flight, and registration would then fail
            # with nothing logged at all.
            self._platform_link_task = asyncio.create_task(
                self._link_to_platform(successful_agents))
            self._track_background_task(self._platform_link_task, "platform link")

            # Monitor processes (blocking call until shutdown)
            await self._monitor()

        finally:
            # Ensure graceful shutdown
            await self.shutdown()

    async def _link_to_platform(self, agent_ids: list[str]) -> None:
        """Register the deployment version, then connect to the platform.

        The connection announces a deployment version rather than an agent, so it
        only means anything once the platform knows that version exists. Opened
        unconditionally it claimed a healthy link on behalf of a deployment that
        would never receive traffic, and sat in the log directly under the warning
        saying registration had failed - which is how an operator reads past it.

        Registration retries transient failures for as long as this runs, so this
        can sit here for the life of the deployment and open the socket the moment
        a tunnel or an ingress starts routing. It returns early only when there is
        no platform to reach, or when the platform refuses the version outright.
        """
        if not api_settings.has_credentials:
            # Not a degraded deployment but a different mode of running one, and
            # the only one the retry loop below cannot survive: with no credentials
            # every attempt raises identically, so retrying announces a permanent
            # local setup as an outage, once at startup and then forever.
            logger.info(
                "AION_CLIENT_ID / AION_CLIENT_SECRET are not set, so this "
                "deployment has no identity on the platform: the agents serve "
                "locally, no version is registered and no platform connection is "
                "opened")
            return

        registered = await AionDeploymentRegisterVersionService().execute(agent_ids)
        if not registered:
            logger.error(
                "The agents are serving locally but the platform refused the "
                "deployment version, so they will receive no platform traffic and "
                "no connection to the platform was opened - restart once the cause "
                "above is resolved")
            return

        self._websocket_manager = AionWebSocketManager(
            ws_transport_factory=WebsocketTransportFactory(
                ws_url=api_settings.ws_gql_url,
                auth_manager=aion_services.AionAuthManagerService(
                    jwt_manager=aion_jwt_manager),
            )
        )
        await aion_services.AionWebSocketService(
            websocket_manager=self._websocket_manager).start_connection()

    async def _stop_platform_link(self) -> None:
        """Close the platform connection, dropping a registration still in flight."""
        task, self._platform_link_task = self._platform_link_task, None
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        manager, self._websocket_manager = self._websocket_manager, None
        if manager is not None:
            await aion_services.AionWebSocketService(
                websocket_manager=manager).stop_connection()

    async def _startup(
            self,
            config: AionConfig,
            proxy_port: int | None = None,
            port_range_start: int = 8000,
            port_range_end: int = 9000,
            proxy_port_search_start: int = 8000,
            proxy_port_search_end: int = 8100,
            startup_timeout: int = 30
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
            startup_timeout: Timeout in seconds for startup confirmation (0 to skip)

        Returns:
            tuple: (successful_agents, failed_agents, proxy_started)
        """
        # Store config for later use
        self.config = config

        # Prepare environment BEFORE starting agents
        self.env_context = env_context = await ServeEnvironmentPreparerService().execute()
        # The API host belongs on this line: the same credentials fail against one
        # environment and succeed against another, and nothing else in the startup
        # log says which one was targeted.
        logger.info(
            "Environment prepared. VERSION_ID: %s | Aion API: %s | client_id: %s | "
            "platform auth: %s",
            env_context.version_id,
            api_settings.http_url,
            api_settings.client_id or "<unset>",
            "available" if env_context.auth_available else "unavailable")

        # Initialize port reservation manager
        self.port_manager = AionPortManager()

        if proxy_port is None:
            # Auto-find proxy port
            found_proxy_port = self.port_manager.reserve_proxy_from_range(
                proxy_port_search_start,
                proxy_port_search_end
            )
            if found_proxy_port is None:
                logger.error(
                    f"Failed to auto-find proxy port in range {proxy_port_search_start}-{proxy_port_search_end}")
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
            port_manager=self.port_manager,
            startup_timeout=startup_timeout
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
                port_manager=self.port_manager,
                startup_timeout=startup_timeout
            )
            if not self.proxy_started:
                logger.error("Failed to start proxy server")

        # Print welcome message after successful startup
        try:
            print(generate_welcome_message(port_manager=self.port_manager))
        except:
            pass

        return self.successful_agents, self.failed_agents, self.proxy_started

    async def _monitor(self) -> None:
        """
        Monitor running processes and handle restarts.

        This is a blocking call that runs until all agents stop or shutdown is requested.
        Uses internal state from startup() call.

        Returns as soon as either the monitoring service finishes on its own or
        a shutdown signal arrives (see :meth:`_setup_signal_handlers`). The
        actual teardown is left to the caller's ``finally``, so it happens once
        and on the event loop rather than inside a signal handler.

        Raises:
            RuntimeError: If called before startup()
        """
        if not self.process_manager or not self.config:
            raise RuntimeError("_monitor() called before _startup()")

        monitoring = asyncio.ensure_future(
            ServeMonitoringService().execute(
                successful_agents=self.successful_agents,
                proxy_started=self.proxy_started,
                config=self.config,
                process_manager=self.process_manager,
            )
        )
        signalled = asyncio.ensure_future(self._shutdown_requested.wait())
        try:
            await asyncio.wait(
                {monitoring, signalled}, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for task in (monitoring, signalled):
                if not task.done():
                    task.cancel()
            # Surface an error the monitor raised; a cancelled task is expected.
            for task in (monitoring, signalled):
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def shutdown(self, timeout: int = 30) -> bool:
        """
        Gracefully shutdown all processes and release reserved ports.

        Args:
            timeout: Maximum time in seconds to wait for processes to shutdown

        Returns:
            bool: True if all processes shut down successfully
        """
        shutdown_successful = True

        await self._stop_platform_link()

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

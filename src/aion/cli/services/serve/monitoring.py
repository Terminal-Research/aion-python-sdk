"""Service for monitoring and restarting AION processes"""
import asyncio
from collections.abc import Awaitable, Callable

from aion.server.services import BaseExecuteService
from aion.server.utils.processes import ProcessManager


class ServeMonitoringService(BaseExecuteService):
    """
    Service for monitoring running AION serve processes and handling restarts.

    This service continuously monitors agent and proxy processes,
    cleaning up dead processes and restarting the proxy if needed.
    """

    check_interval: float = 10.0
    """Seconds between two checks of the managed processes."""

    async def execute(
        self,
        successful_agents: list[str],
        proxy_started: bool,
        process_manager: ProcessManager,
        restart_proxy: Callable[[], Awaitable[bool]],
    ) -> None:
        """
        Monitor running processes and handle restarts.

        Args:
            successful_agents: List of successfully started agent IDs
            proxy_started: Whether proxy was started initially
            process_manager: ProcessManager instance
            restart_proxy: Starts the proxy again on its reserved port;
                resolves to True once the new proxy confirms its startup
        """
        proxy_alive = proxy_started

        try:
            while True:
                # Use asyncio.sleep for async compatibility
                await asyncio.sleep(self.check_interval)

                # Clean up any dead processes
                process_manager.cleanup_dead_processes()

                # Check if all agents are still alive
                alive_count = sum(
                    1
                    for agent_id in successful_agents
                    if process_manager.get_process_info(agent_id)
                    and process_manager.get_process_info(agent_id).process.is_alive()
                )

                # Check proxy status
                if proxy_alive:
                    proxy_info = process_manager.get_process_info("proxy")
                    proxy_alive = proxy_info and proxy_info.process.is_alive()

                # Exit if all agents have stopped
                if alive_count == 0:
                    self.logger.error("All agents have stopped, exiting...")
                    break

                # Restart proxy if it died but agents are still running
                if proxy_started and not proxy_alive and alive_count > 0:
                    self.logger.warning("Proxy server died, attempting to restart...")
                    if await restart_proxy():
                        self.logger.debug("Proxy server restarted successfully")
                        proxy_alive = True
                    else:
                        self.logger.error("Failed to restart proxy server")
                        proxy_alive = False

        except KeyboardInterrupt:
            self.logger.debug("Received shutdown signal...")
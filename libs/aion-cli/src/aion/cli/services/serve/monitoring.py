"""Service for monitoring and restarting AION processes"""
import asyncio

from aion.shared.config import AionConfig
from aion.shared.services import BaseExecuteService
from aion.shared.utils.processes import ProcessManager


class ServeMonitoringService(BaseExecuteService):
    """
    Service for monitoring running AION serve processes and handling restarts.

    This service continuously monitors agent and proxy processes,
    cleaning up dead processes and restarting the proxy if needed.
    """

    async def execute(
        self,
        successful_agents: list[str],
        proxy_started: bool,
        config: AionConfig,
        process_manager: ProcessManager,
    ) -> None:
        """
        Monitor running processes and handle restarts.

        Args:
            successful_agents: List of successfully started agent IDs
            proxy_started: Whether proxy was started initially
            config: AION configuration instance
            process_manager: ProcessManager instance
        """
        proxy_alive = proxy_started

        try:
            while True:
                # Use asyncio.sleep for async compatibility
                await asyncio.sleep(10)

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
                    if await self._restart_proxy(config, process_manager):
                        self.logger.debug("Proxy server restarted successfully")
                        proxy_alive = True
                    else:
                        self.logger.error("Failed to restart proxy server")
                        proxy_alive = False

        except KeyboardInterrupt:
            self.logger.debug("Received shutdown signal...")

    @staticmethod
    async def _restart_proxy(config: AionConfig, process_manager: ProcessManager) -> bool:
        """
        Restart the proxy server.

        Args:
            config: AION configuration
            process_manager: ProcessManager instance

        Returns:
            bool: True if proxy restarted successfully
        """
        from .proxy_startup import ServeProxyStartupService

        return await ServeProxyStartupService().execute(config, process_manager)
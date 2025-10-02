"""Server management handler for AION agents and proxy"""
import asyncio
import os
import signal
import sys
from typing import Dict, Optional

from aion.proxy import AionAgentProxyServer
from aion.shared.aion_config import AgentConfig
from aion.shared.logging import get_logger
from aion.shared.utils.processes import ProcessManager

logger = get_logger("ServerManager")


class ServerManager:
    """Manages AION agents and proxy server processes"""

    def __init__(self):
        self.process_manager: Optional[ProcessManager] = None
        self.agent_configs: Dict[str, AgentConfig] = {}
        self._setup_signal_handlers()

    def _setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        logger.info(f"Received signal {signum}, shutting down all agents and proxy...")
        if self.process_manager:
            self.process_manager.shutdown_all(timeout=30)
        sys.exit(0)

    def initialize(self):
        """Initialize the process manager"""
        self.process_manager = ProcessManager()

    def start_agent(self, agent_id: str, agent_config: AgentConfig) -> bool:
        """
        Start an agent in a separate process

        Args:
            agent_id: Unique identifier for the agent
            agent_config: Agent configuration

        Returns:
            bool: True if agent started successfully
        """
        if not self.process_manager:
            return False

        # Store agent configuration
        self.agent_configs[agent_id] = agent_config

        # Create and start the process
        success = self.process_manager.create_process(
            key=agent_id,
            target_function=self._agent_wrapper,
            agent_id=agent_id,
            agent_config=agent_config
        )

        if success:
            logger.info(f"Agent '{agent_id}' started successfully")
        else:
            logger.error(f"Failed to start agent '{agent_id}'")
            self.agent_configs.pop(agent_id, None)

        return success

    def start_proxy(self, config) -> bool:
        """
        Start proxy server in a separate process

        Args:
            config: AionConfig instance

        Returns:
            bool: True if proxy started successfully
        """
        if not self.process_manager:
            return False

        # Create and start the proxy process
        success = self.process_manager.create_process(
            key="proxy",
            target_function=self._proxy_wrapper,
            config=config
        )

        if success:
            logger.info(f"Proxy server started successfully on port {config.proxy.port}")
        else:
            logger.error("Failed to start proxy server")

        return success

    def start_all_agents(self, config) -> tuple[list[str], list[str]]:
        """
        Start all configured agents

        Args:
            config: AionConfig instance

        Returns:
            tuple: (successful_agents, failed_agents)
        """
        successful_agents = []
        failed_agents = []

        logger.info(f"Starting {len(config.agents)} AION agents...")

        for agent_id, agent_config in config.agents.items():
            if self.start_agent(agent_id, agent_config):
                successful_agents.append(agent_id)
            else:
                failed_agents.append(agent_id)

        return successful_agents, failed_agents

    async def monitor_processes(self, successful_agents: list[str], proxy_started: bool, config) -> None:
        """
        Monitor running processes and handle restarts

        Args:
            successful_agents: List of successfully started agent IDs
            proxy_started: Whether proxy was started
            config: AionConfig instance
        """
        proxy_alive = proxy_started

        try:
            while True:
                # Use asyncio.sleep for async compatibility
                await asyncio.sleep(10)

                # Clean up any dead processes
                self.process_manager.cleanup_dead_processes()

                # Check if all agents are still alive
                alive_count = sum(1 for agent_id in successful_agents
                                  if self.process_manager.get_process_info(agent_id)
                                  and self.process_manager.get_process_info(agent_id).process.is_alive())

                # Check proxy status
                if proxy_alive:
                    proxy_info = self.process_manager.get_process_info("proxy")
                    proxy_alive = proxy_info and proxy_info.process.is_alive()

                # Exit if all agents have stopped
                if alive_count == 0:
                    logger.error("All agents have stopped, exiting...")
                    break

                # Restart proxy if it died but agents are still running
                if proxy_started and not proxy_alive and alive_count > 0:
                    logger.warning("Proxy server died, attempting to restart...")
                    if self.start_proxy(config):
                        logger.info("Proxy server restarted successfully")
                        proxy_alive = True
                    else:
                        logger.error("Failed to restart proxy server")
                        proxy_alive = False

        except KeyboardInterrupt:
            logger.info("Received shutdown signal...")

    def shutdown(self) -> bool:
        """
        Gracefully shutdown all processes

        Returns:
            bool: True if all processes shut down successfully
        """
        logger.info("Shutting down all agents and proxy...")
        if self.process_manager:
            success = self.process_manager.shutdown_all(timeout=30)
            if success:
                logger.info("All processes shut down successfully")
            else:
                logger.warning("Some processes may not have shut down cleanly")
            return success
        return True

    def get_process_status(self) -> Dict[str, Dict]:
        """
        Get status of all managed processes

        Returns:
            dict: Process status information
        """
        if not self.process_manager:
            return {}

        status = {}

        # Get agent statuses
        for agent_id in self.agent_configs.keys():
            info = self.process_manager.get_process_info(agent_id)
            if info:
                status[agent_id] = {
                    'type': 'agent',
                    'pid': info.process.pid,
                    'alive': info.process.is_alive(),
                    'started_at': info.started_at
                }

        # Get proxy status
        proxy_info = self.process_manager.get_process_info("proxy")
        if proxy_info:
            status['proxy'] = {
                'type': 'proxy',
                'pid': proxy_info.process.pid,
                'alive': proxy_info.process.is_alive(),
                'started_at': proxy_info.started_at
            }

        return status

    @staticmethod
    def _agent_wrapper(agent_id: str, agent_config: AgentConfig):
        """
        Wrapper function to run agent server in subprocess

        Args:
            agent_id: Agent identifier
            agent_config: Agent configuration
        """
        try:
            from aion.server import run_server
            logger.info(f"Starting agent '{agent_id}' in process {os.getpid()}")

            # Create new event loop for this process
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                # Run the async server function
                loop.run_until_complete(run_server(agent_id=agent_id, agent_config=agent_config))
            finally:
                loop.close()

        except Exception as e:
            logger.error(f"Agent '{agent_id}' crashed: {str(e)}")
            raise

    @staticmethod
    def _proxy_wrapper(config):
        """
        Wrapper function to run proxy server in subprocess

        Args:
            config: AionConfig instance
        """
        try:
            # Create new event loop for this process
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            proxy_server = AionAgentProxyServer(config)

            try:
                # Run the async proxy server function
                loop.run_until_complete(proxy_server.start())
            finally:
                loop.close()

        except Exception as e:
            logger.error(f"Proxy server crashed: {str(e)}")
            raise

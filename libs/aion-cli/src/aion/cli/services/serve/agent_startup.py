"""Service for starting AION agent processes"""
import asyncio

from aion.shared.aion_config import AionConfig, AgentConfig
from aion.shared.base import BaseExecuteService
from aion.shared.utils.processes import ProcessManager


class ServeAgentStartupService(BaseExecuteService):
    """
    Service for starting and managing AION agent processes for the serve command.

    This service handles the initialization and startup of all configured agents,
    creating separate processes for each agent and tracking their startup success.
    """

    async def execute(
            self,
            config: AionConfig,
            process_manager: ProcessManager
    ) -> tuple[list[str], list[str]]:
        """
        Start all configured agents in separate processes.

        Args:
            config: AION configuration containing agent definitions
            process_manager: ProcessManager instance to create agent processes

        Returns:
            tuple: (successful_agents, failed_agents) - lists of agent IDs
        """
        successful_agents = []
        failed_agents = []

        self.logger.debug(f"Starting {len(config.agents)} AION agents...")

        for agent_id, agent_config in config.agents.items():
            if await self._start_agent(agent_id, agent_config, process_manager):
                successful_agents.append(agent_id)
            else:
                failed_agents.append(agent_id)

        return successful_agents, failed_agents

    async def _start_agent(
            self,
            agent_id: str,
            agent_config: AgentConfig,
            process_manager: ProcessManager
    ) -> bool:
        """
        Start a single agent in a separate process.

        Args:
            agent_id: Unique identifier for the agent
            agent_config: Agent configuration
            process_manager: ProcessManager instance

        Returns:
            bool: True if agent started successfully
        """
        # Create and start the process
        success = process_manager.create_process(
            key=agent_id,
            target_function=self._agent_wrapper,
            agent_id=agent_id,
            agent_config=agent_config,
        )

        if not success:
            self.logger.error(f"Failed to start agent '{agent_id}'")

        return success

    @staticmethod
    def _agent_wrapper(agent_id: str, agent_config: AgentConfig):
        """
        Wrapper function to run agent server in subprocess.

        Args:
            agent_id: Agent identifier
            agent_config: Agent configuration
        """
        from aion.shared.logging import get_logger

        logger = get_logger()

        try:
            from aion.server import run_server

            # Create new event loop for this process
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                # Run the async server function
                loop.run_until_complete(
                    run_server(agent_id=agent_id, agent_config=agent_config)
                )
            finally:
                loop.close()

        except Exception as e:
            logger.error(f"Agent '{agent_id}' crashed: {str(e)}")
            raise

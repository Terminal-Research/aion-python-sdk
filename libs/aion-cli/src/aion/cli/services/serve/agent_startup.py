"""Service for starting AION agent processes"""
import asyncio
import os

from aion.server import run_server
from aion.shared.aion_config import AionConfig, AgentConfig
from aion.shared.base import BaseExecuteService
from aion.shared.logging import get_logger
from aion.shared.utils.processes import ProcessManager

logger = get_logger()


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
        Start all configured agents in separate processes in parallel.

        Args:
            config: AION configuration containing agent definitions
            process_manager: ProcessManager instance to create agent processes

        Returns:
            tuple: (successful_agents, failed_agents) - lists of agent IDs
        """
        successful_agents = []
        failed_agents = []

        self.logger.debug(f"Starting {len(config.agents)} AION agents in parallel...")

        # Create tasks for starting all agents in parallel
        tasks = [
            self._start_agent(agent_id, agent_config, process_manager)
            for agent_id, agent_config in config.agents.items()
        ]

        # Wait for all agents to start
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        for agent_id, result in zip(config.agents.keys(), results):
            if isinstance(result, Exception):
                self.logger.error(f"Agent '{agent_id}' failed with exception: {result}")
                failed_agents.append(agent_id)
            elif result:
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
        Start a single agent in a separate process and wait for startup confirmation.

        Args:
            agent_id: Unique identifier for the agent
            agent_config: Agent configuration
            process_manager: ProcessManager instance

        Returns:
            bool: True if agent started successfully
        """
        # Create and start the process with pipe for communication
        success = process_manager.create_process(
            key=agent_id,
            func=self._agent_wrapper,
            func_kwargs={
                "agent_id": agent_id,
                "agent_config": agent_config,
            },
            use_pipe=True
        )

        if not success:
            self.logger.error(f"Failed to start agent '{agent_id}'")
            return False

        # Wait for startup confirmation from agent server
        self.logger.debug(f"Waiting for agent '{agent_id}' startup confirmation...")
        startup_message = await asyncio.to_thread(
            process_manager.receive_from_process, agent_id, 30.0  # 30 second timeout
        )

        if startup_message and startup_message.get("status") == "started":
            self.logger.debug(f"Agent '{agent_id}' started successfully")
            return True
        else:
            self.logger.error(f"Agent '{agent_id}' failed to send startup confirmation")
            return False

    @staticmethod
    def _send_startup_event(agent_id: str, conn):
        """
        Send startup confirmation to parent process.

        Args:
            agent_id: Agent identifier
            conn: Pipe connection to parent process
        """
        if conn is not None:
            try:
                conn.send({"status": "started", "pid": os.getpid(), "agent_id": agent_id})
                logger.debug(f"Sent startup confirmation for agent '{agent_id}' to parent process")
            except Exception as ex:
                logger.warning(f"Failed to send startup confirmation for agent '{agent_id}': {str(ex)}")

    @staticmethod
    def _agent_wrapper(agent_id: str, agent_config: AgentConfig, conn=None):
        """
        Wrapper function to run agent server in subprocess.

        Args:
            agent_id: Agent identifier
            agent_config: Agent configuration
            conn: Pipe connection to parent process (optional)
        """
        try:
            # Create new event loop for this process
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                # Run the async server function with startup callback
                loop.run_until_complete(
                    run_server(
                        agent_id=agent_id,
                        agent_config=agent_config,
                        startup_callback=lambda: ServeAgentStartupService._send_startup_event(agent_id, conn)
                    )
                )
            finally:
                loop.close()

        except Exception as e:
            logger.error(f"Agent '{agent_id}' crashed: {str(e)}")
            # Send error status to parent process if connection exists
            if conn is not None:
                try:
                    conn.send({"status": "error", "error": str(e), "agent_id": agent_id})
                except Exception:
                    pass
            raise

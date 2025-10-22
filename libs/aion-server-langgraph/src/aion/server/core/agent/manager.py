"""Agent manager for single-agent deployment.

This module provides the AgentManager class for managing a single agent instance
in the AION server. For multi-agent scenarios, deploy multiple servers behind
a proxy instead.
"""

from typing import Optional

from aion.shared.aion_config.models import AgentConfig
from aion.shared.logging import get_logger
from aion.shared.metaclasses import Singleton

from .agent import AionAgent

logger = get_logger()


class AgentManager(metaclass=Singleton):
    """Manager for single agent instance.

    This class manages the lifecycle of a single agent in the AION server.
    It ensures only one agent is active at a time and provides access to
    the current agent instance.

    For multi-agent scenarios, deploy multiple server instances on different
    ports and route requests through a proxy.
    """

    def __init__(self):
        """Initialize agent manager."""
        self._agent: Optional[AionAgent] = None
        self._agent_id: Optional[str] = None
        self._agent_config: Optional[AgentConfig] = None

    @property
    def agent(self) -> Optional[AionAgent]:
        """Get current agent instance.

        Returns:
            Optional[AionAgent]: Current agent or None if no agent is loaded
        """
        return self._agent

    @property
    def agent_id(self) -> Optional[str]:
        """Get current agent ID.

        Returns:
            Optional[str]: Current agent ID or None if no agent is loaded
        """
        return self._agent_id

    @property
    def agent_config(self) -> Optional[AgentConfig]:
        """Get current agent configuration.

        Returns:
            Optional[AgentConfig]: Current agent config or None if no agent is loaded
        """
        return self._agent_config

    @property
    def is_loaded(self) -> bool:
        """Check if an agent is currently loaded.

        Returns:
            bool: True if agent is loaded, False otherwise
        """
        return self._agent is not None

    def set_agent_config(self, agent_id: str, config: AgentConfig) -> None:
        """Set agent configuration without creating the agent.

        This allows you to prepare the configuration first, then call
        create_agent() without parameters later.

        Args:
            agent_id: Unique agent identifier
            config: Agent configuration
        """
        self._agent_id = agent_id
        self._agent_config = config
        logger.debug(f"Agent configuration set for '{agent_id}'")

    async def create_agent(
            self,
            agent_id: Optional[str] = None,
            config: Optional[AgentConfig] = None,
    ) -> AionAgent:
        """Create and register a new agent.

        Can be called in two ways:
        1. With parameters: create_agent(agent_id, config)
        2. Without parameters after set_agent_config(): create_agent()

        Args:
            agent_id: Unique agent identifier (optional if set via set_agent_config)
            config: Agent configuration (optional if set via set_agent_config)

        Returns:
            AionAgent: Created agent instance

        Raises:
            RuntimeError: If an agent is already loaded
            ValueError: If configuration is not provided and not set
        """
        if self._agent is not None:
            raise RuntimeError(
                f"Agent '{self._agent_id}' is already loaded. "
                f"Call clear() first to replace it."
            )

        # Use provided parameters or fall back to stored config
        final_agent_id = agent_id or self._agent_id
        final_config = config or self._agent_config

        if not final_agent_id or not final_config:
            raise ValueError(
                "Agent ID and config must be provided either as parameters "
                "or via set_agent_config() before calling create_agent()"
            )

        logger.info(f"Creating agent '{final_agent_id}'...")

        # Create agent using auto-detection
        agent = await AionAgent.from_config(agent_id=final_agent_id, config=final_config)

        # Store agent
        self._agent = agent
        self._agent_id = final_agent_id
        self._agent_config = final_config

        logger.info(
            f"Agent '{final_agent_id}' created and loaded "
            f"(framework={agent.framework}, "
            f"streaming={agent.supports_streaming()}, "
            f"resume={agent.supports_resume()})"
        )

        return agent

    def get_agent(self) -> Optional[AionAgent]:
        """Get current agent instance.

        Returns:
            Optional[AionAgent]: Current agent or None if no agent is loaded
        """
        return self._agent

    def get_agent_or_raise(self) -> AionAgent:
        """Get current agent or raise if not loaded.

        Returns:
            AionAgent: Current agent instance

        Raises:
            RuntimeError: If no agent is loaded
        """
        if self._agent is None:
            raise RuntimeError("No agent is currently loaded")
        return self._agent

    def clear(self) -> None:
        """Clear current agent.

        This removes the current agent from the manager, allowing a new
        agent to be created.

        Note:
            This does not clean up resources held by the agent. The agent
            may continue running if there are active references to it.
        """
        if self._agent is not None:
            logger.info(f"Clearing agent '{self._agent_id}'")
            self._agent = None
            self._agent_id = None
            self._agent_config = None
        else:
            logger.debug("No agent to clear")

    async def reload_agent(self, config: AgentConfig) -> AionAgent:
        """Reload agent with new configuration.

        This is a convenience method that clears the current agent and
        creates a new one with the provided configuration.

        Args:
            config: New agent configuration

        Returns:
            AionAgent: Newly created agent instance

        Raises:
            ValueError: If configuration is invalid
            FileNotFoundError: If agent module not found
        """
        agent_id = config.id

        logger.info(f"Reloading agent '{agent_id}'...")

        # Clear existing agent
        self.clear()

        # Create new agent
        return await self.create_agent(agent_id, config)

    def __repr__(self) -> str:
        """Return string representation of the manager."""
        if self._agent is None:
            return "AgentManager(agent=None)"
        return f"AgentManager(agent={self._agent!r})"


agent_manager = AgentManager()

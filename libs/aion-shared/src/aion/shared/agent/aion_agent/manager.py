"""Agent manager for single-agent deployment.

This module provides the AgentManager class for managing a single agent instance
in the AION server. For multi-agent scenarios, deploy multiple servers behind
a proxy instead.
"""
from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from aion.shared.config.models import AgentConfig
from aion.shared.metaclasses import Singleton

from .agent import AionAgent

if TYPE_CHECKING:
    from aion.shared.logging.base import AionLogger


class AgentManager(metaclass=Singleton):
    """Manager for single agent instance.

    This class manages the lifecycle of a single agent in the AION server.
    It ensures only one agent is active at a time and provides access to
    the current agent instance.

    For multi-agent scenarios, deploy multiple server instances on different
    ports and route requests through a proxy.
    """

    def __init__(self, logger: Optional[AionLogger] = None):
        """Initialize agent manager."""
        self._agent: Optional[AionAgent] = None
        self._agent_id: Optional[str] = None
        self._agent_config: Optional[AgentConfig] = None
        self._logger: Optional[AionLogger] = logger

    @property
    def logger(self) -> AionLogger:
        if not self._logger:
            from aion.shared.logging.factory import get_logger
            self._logger = get_logger()
        return self._logger

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

    async def create_agent(
            self,
            agent_id: Optional[str] = None,
            port: Optional[int] = None,
            config: Optional[AgentConfig] = None,
    ) -> AionAgent:
        """Create and register a new agent (without building).

        This creates an agent instance but does NOT discover the framework yet.
        Call agent.build() after plugins are registered to complete initialization.

        Can be called in two ways:
        1. With parameters: create_agent(agent_id, config)
        2. Without parameters after set_agent_config(): create_agent()

        Args:
            agent_id: Unique agent identifier (optional if set via set_agent_config)
            port: Optional port number to bind to (optional)
            config: Agent configuration (optional if set via set_agent_config)

        Returns:
            AionAgent: Created agent instance (not yet built)

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

        # set global info about agent
        if agent_id or config:
            self.set_agent_config(final_agent_id, final_config)

        self.logger.info(f"Creating agent '{final_agent_id}' (framework discovery deferred)...")

        # Create agent WITHOUT framework discovery
        agent = AionAgent(agent_id=final_agent_id, config=final_config)
        agent.port = port

        # Store agent
        self._agent = agent
        return agent

    def get_agent(self, raise_: bool = False) -> Optional[AionAgent]:
        """Get current agent instance.

        Returns:
            Optional[AionAgent]: Current agent or None if no agent is loaded

        Raises:
            RuntimeError: If no agent is loaded (if raise_ is True)
        """
        if not self._agent and raise_:
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
            self.logger.info(f"Clearing agent '{self._agent_id}'")
            self._agent = None
            self._agent_id = None
            self._agent_config = None
        else:
            self.logger.debug("No agent to clear")

    def __repr__(self) -> str:
        """Return string representation of the manager."""
        if self._agent is None:
            return "AgentManager(agent=None)"
        return f"AgentManager(agent={self._agent!r})"


agent_manager = AgentManager()

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from aion.shared.aion_config import AgentConfig

from .base import BaseAgent
from .factory import AgentFactory

logger = logging.getLogger(__name__)


class AgentManager:
    """Manager for handling a single agent instance."""

    def __init__(self, base_path: Optional[Path] = None, logger_: Optional[logging.Logger] = None):
        """Initialize manager with agent factory.

        Args:
            base_path: Base path for resolving module imports
            logger_: Logger instance to use
        """
        self.factory = AgentFactory(base_path=base_path, logger_=logger_)
        self.agent: Optional[BaseAgent] = None
        self.agent_id: Optional[str] = None
        self.logger = logger_ or logging.getLogger(__name__)

    def create_agent(self, agent_id: str, agent_config: AgentConfig) -> BaseAgent:
        """Create and store an agent from configuration.

        Args:
            agent_id: Unique identifier for the agent
            agent_config: Complete agent configuration

        Returns:
            Created BaseAgent instance

        Raises:
            ValueError: If agent creation fails
            RuntimeError: If an agent is already created
        """
        if self.agent is not None:
            raise RuntimeError(f"Agent '{self.agent_id}' is already created.")

        self.agent = self.factory.create_agent_from_config(agent_id, agent_config)
        self.agent_id = agent_id
        return self.agent

    def get_agent(self) -> Optional[BaseAgent]:
        """Get the current agent instance.

        Returns:
            BaseAgent instance or None if no agent is created
        """
        return self.agent

    def get_agent_id(self) -> Optional[str]:
        """Get the current agent ID.

        Returns:
            Agent ID or None if no agent is created
        """
        return self.agent_id

    def has_agent(self) -> bool:
        """Check if an agent is created.

        Returns:
            True if agent is created, False otherwise
        """
        return self.agent is not None

    def precompile_agent(self) -> bool:
        """Pre-compile the agent's graph.

        Returns:
            True if compilation succeeded, False otherwise
        """
        if not self.agent:
            self.logger.warning("No agent to pre-compile")
            return False

        try:
            # Pre-compile the graph to catch any issues early
            self.agent.get_compiled_graph()
            self.logger.info("Successfully pre-compiled graph for agent '%s'", self.agent_id)
            return True
        except Exception as e:
            self.logger.error("Failed to pre-compile agent '%s': %s", self.agent_id, e)
            return False

    def clear_agent(self) -> None:
        """Clear the current agent."""
        if self.agent:
            self.logger.info("Cleared agent '%s'", self.agent_id)
        self.agent = None
        self.agent_id = None


# Global instance
agent_manager = AgentManager()

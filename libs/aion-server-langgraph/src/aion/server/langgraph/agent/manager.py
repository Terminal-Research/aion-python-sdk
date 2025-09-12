from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Dict, Optional, Union

from langgraph.graph import Graph
from langgraph.pregel import Pregel

from aion.server.core.metaclasses import Singleton
from .base import BaseAgent
from .config_processor import AgentConfigProcessor
from aion.shared.utils import get_config_path

logger = logging.getLogger(__name__)


class AgentManager(metaclass=Singleton):
    """Manages agent registration and loading."""

    def __init__(self):
        self.agents: Dict[str, BaseAgent] = {}

    def set_agent(self, agent_id: str, agent: BaseAgent) -> None:
        """Register an agent object by ID.

        Args:
            agent_id: Unique identifier for the agent.
            agent: Agent object that inherits from BaseAgent.

        Raises:
            TypeError: If agent is not an instance of BaseAgent.
        """
        if not isinstance(agent, BaseAgent):
            raise TypeError(f"Agent must be an instance of BaseAgent, got {type(agent)}")

        if self.agents.get(agent_id):
            logger.info(f"Updating agent \"{agent_id}\"")
        else:
            logger.info(f"Registering agent \"{agent_id}\"")

        self.agents[agent_id] = agent

    def get_agent(self, agent_id: str) -> Optional[BaseAgent]:
        """Return a registered agent.

        Args:
            agent_id: Unique identifier of the agent to retrieve.

        Returns:
            The registered agent object or None if not found.
        """
        return self.agents.get(agent_id)

    def get_agent_card(self, agent_id: str):
        agent = self.get_agent(agent_id)
        if not agent:
            return

    def get_compiled_graph(self, agent_id: str) -> Optional[Union[Graph, Pregel]]:
        """Return a compiled graph for the agent.

        Args:
            agent_id: Unique identifier of the agent.

        Returns:
            The compiled graph object or None if agent not found.
        """
        agent = self.get_agent(agent_id)
        return agent.get_compiled_graph() if agent else None

    def get_first_agent(self) -> Optional[BaseAgent]:
        """Return the first registered agent."""
        if self.agents:
            return next(iter(self.agents.values()))
        return None

    def recompile(self, agent_id: str) -> Optional[Union[Graph, Pregel]]:
        """Force recompilation of agent's graph.

        Args:
            agent_id: Unique identifier of the agent to recompile.

        Returns:
            The newly compiled graph object or None if agent not found.
        """
        agent = self.get_agent(agent_id)
        return agent.recompile() if agent else None

    def list_agents(self) -> Dict[str, str]:
        """Return a dictionary of agent IDs and their class names."""
        return {agent_id: agent.__class__.__name__ for agent_id, agent in self.agents.items()}

    def _precompile_agent(self, agent_id: str) -> None:
        """Pre-compile a single agent's graph during initialization.

        Args:
            agent_id: The unique identifier of the agent to compile.
        """
        agent = self.get_agent(agent_id)
        if not agent:
            logger.warning(f"Agent \"{agent_id}\" was not found for pre-compilation.")
            return

        try:
            # Pre-compile the graph to catch any issues early
            agent.get_compiled_graph()
            logger.info(f"Successfully pre-compiled graph for agent \"{agent_id}\"")
        except Exception as e:
            logger.error(f"Failed to pre-compile agent '{agent_id}': {e}")
            # Don't re-raise here to allow other agents to initialize
            # The error will be raised when the graph is actually used

    def precompile_all(self) -> None:
        """Pre-compile graphs for all agents in the collection."""
        for agent_id in self.agents.keys():
            self._precompile_agent(agent_id)

    def initialize_agents(self, config_path: str | Path = "aion.yaml") -> None:
        """Load and register agents declared in aion.yaml.

        Args:
            config_path: Path to the configuration file. Defaults to aion.yaml
                in the current working directory.
        """
        config_path = get_config_path(config_path)
        logger.info("Loading agents from %s", config_path)

        # load and process agents
        processor = AgentConfigProcessor(config_path=config_path, logger_=logger)

        try:
            agents = processor.load_and_process_config()

            if not agents:
                logger.warning("No agents configured in %s", config_path)
                return

            # Register all loaded agents
            for agent_id, agent_instance in agents.items():
                self.set_agent(agent_id=agent_id, agent=agent_instance)

            self.precompile_all()
            logger.info("Initialized %d agents", len(agents))

        except Exception as e:
            logger.error(f"Failed to initialize agents: {e}")
            raise

    def has_active_agents(self) -> bool:
        """Check if any agents are loaded."""
        return bool(self.agents)


# Global instance
agent_manager = AgentManager()

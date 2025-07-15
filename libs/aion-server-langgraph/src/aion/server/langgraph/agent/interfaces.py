from abc import ABC, abstractmethod
from typing import Union, Optional, Callable

from a2a.types import AgentCard
from langgraph.graph import Graph
from langgraph.pregel import Pregel
from .models import AgentConfig


class AgentInterface(ABC):
    """Interface defining the contract for all agents."""

    @abstractmethod
    def get_graph(self) -> Union[Graph, Pregel]:
        """Return the agent's graph.

        Returns:
            The agent's graph object.
        """
        pass

    @abstractmethod
    def get_compiled_graph(self) -> Union[Graph, Pregel]:
        """Return the agent's compiled graph.

        Returns:
            The agent's compiled graph object.
        """
        pass

    @abstractmethod
    def create_graph(self) -> Union[Graph, Pregel]:
        """Create and return the agent's graph.

        Returns:
            Newly created graph object.
        """
        pass

    @abstractmethod
    def create_compiled_graph(self) -> Union[Graph, Pregel]:
        """Create and return a compiled graph for the agent.

        Returns:
            Newly compiled graph object.
        """
        pass

    @abstractmethod
    def recompile(self) -> Union[Graph, Pregel]:
        """Force recompilation of the agent's graph.

        Returns:
            Newly compiled graph object.
        """
        pass

    @abstractmethod
    def get_agent_card(self, base_url: str) -> Optional[AgentCard]:
        """Return the agent's card with capabilities description.

        Args:
            base_url: Base URL for the agent service.

        Returns:
            Agent card containing metadata and capabilities.
        """
        pass

    @abstractmethod
    def generate_agent_card(self, base_url: str) -> Optional[AgentCard]:
        """Generate the agent's capability card.

        Creates an AgentCard that describes this agent's capabilities,
        including its skills, supported input/output modes, and metadata.
        May be replaced with user's custom card.

        Args:
            base_url: Base URL where this agent is hosted, used to construct
                     the agent's endpoint URL.

        Returns:
            Agent card or None if not implemented.
        """
        pass

    @property
    @abstractmethod
    def config(self) -> Optional[AgentConfig]:
        """Get the agent's configuration.

        Returns:
            Agent configuration or None if not set.
        """
        pass

    @config.setter
    @abstractmethod
    def config(self, value: AgentConfig) -> None:
        """Set the agent's configuration.

        Args:
            value: Agent configuration to set.

        Raises:
            TypeError: If value is not an AgentConfig instance.
        """
        pass

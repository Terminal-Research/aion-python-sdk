from abc import ABC, abstractmethod
from typing import Union, Optional, Callable

from langgraph.graph import Graph
from langgraph.pregel import Pregel

from .card import AionAgentCard

from aion.shared.aion_config import AgentConfig


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

    @property
    @abstractmethod
    def card(self) -> AionAgentCard:
        """Get the agent's card."""
        pass

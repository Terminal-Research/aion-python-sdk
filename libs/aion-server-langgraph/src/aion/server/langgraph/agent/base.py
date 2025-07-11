import logging
from abc import abstractmethod
from typing import Union

from a2a.types import AgentCard
from langgraph.graph import Graph
from langgraph.pregel import Pregel

from .checkpointer import GraphCheckpointerManager
from .interfaces import AgentInterface

logger = logging.getLogger(__name__)


class BaseAgent(AgentInterface):
    """Base class for all agents."""

    def __init__(self):
        self._graph = None
        self._compiled_graph = None

    def get_graph(self) -> Union[Graph, Pregel]:
        """Return the agent's graph with caching."""
        if self._graph is None:
            self._graph = self.create_graph()
        return self._graph

    def get_compiled_graph(self) -> Union[Graph, Pregel]:
        """Return the agent's compiled graph with caching."""
        if self._compiled_graph is None:
            self._compiled_graph = self.create_compiled_graph()
        return self._compiled_graph

    def create_compiled_graph(self) -> Union[Graph, Pregel]:
        """Create and return a compiled graph for the agent."""
        graph = self.get_graph()
        if hasattr(graph, "compile") and callable(getattr(graph, "compile")):
            logger.info(f"Compiling graph for agent \"{self.__class__.__name__}\"")
            checkpointer = GraphCheckpointerManager(graph).get_checkpointer()
            compiled_graph = graph.compile(checkpointer=checkpointer)
            return compiled_graph
        else:
            # If graph doesn't require compilation, return it as is
            logger.info(f"Graph for agent \"{self.__class__.__name__}\" doesn't require compilation")
            return graph

    def recompile(self) -> Union[Graph, Pregel]:
        """Force recompilation of the agent's graph."""
        self._compiled_graph = None
        return self.get_compiled_graph()

    @abstractmethod
    def create_graph(self) -> Union[Graph, Pregel]:
        """Create and return the agent's graph."""
        pass

    @abstractmethod
    def get_agent_card(self, base_url: str) -> AgentCard:
        """Return the agent's card with capabilities description."""
        pass

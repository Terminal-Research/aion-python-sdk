from typing import Union, Optional, Callable

from aion.shared.aion_config import AgentConfig
from aion.shared.logging import get_logger
from langgraph.graph import Graph
from langgraph.pregel import Pregel

from aion.shared.settings import app_settings
from .card import AionAgentCard
from .checkpointer import GraphCheckpointerManager
from .interfaces import AgentInterface

logger = get_logger("BaseAgent")


class BaseAgent(AgentInterface):
    """Base class for all agents."""
    _card: AionAgentCard

    def __init__(
            self,
            graph_source: Optional[Union[Graph, Pregel, Callable[[], Union[Graph, Pregel]]]] = None,
            config: Optional[AgentConfig] = None,
            base_url: Optional[str] = None,
    ):
        """Initialize BaseAgent.

        Args:
            graph_source: Optional graph instance or function that returns a graph
            config: Optional configuration for the agent
        """
        self.agent_id = None
        self._graph = None
        self._compiled_graph = None
        self._graph_source = graph_source
        self._config = config
        self.base_url = base_url or app_settings.url

    @property
    def config(self) -> Optional[AgentConfig]:
        """Get the agent's configuration."""
        return self._config

    @config.setter
    def config(self, value: AgentConfig) -> None:
        """Set the agent's configuration."""
        if value is not None and not isinstance(value, AgentConfig):
            raise TypeError(f"Config must be an AgentConfig instance, got {type(value)}")
        self._config = value

    @property
    def card(self) -> AionAgentCard:
        """Generate agent card from config."""
        if hasattr(self, "_card"):
            return self._card

        self._card = AionAgentCard.from_config(config=self.config, base_url=self.base_url)
        return self._card

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

    def create_graph(self) -> Union[Graph, Pregel]:
        """Create and return the agent's graph."""
        if self._graph_source:
            if callable(self._graph_source):
                # It's a function that returns a graph
                return self._graph_source()
            else:
                # It's already a graph instance
                return self._graph_source
        else:
            # This should be implemented by subclasses
            raise NotImplementedError("Subclasses must implement create_graph or provide graph_source")

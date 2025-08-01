import logging
from typing import Union, Optional, Callable

from a2a.types import AgentCard, AgentExtension
from langgraph.graph import Graph
from langgraph.pregel import Pregel

from .checkpointer import GraphCheckpointerManager
from .interfaces import AgentInterface
from .models import AgentConfig

from .card import AionAgentCard

from aion.server.types import GetContextParams, GetContextsListParams
from aion.server.configs import aion_platform_settings

logger = logging.getLogger(__name__)


class BaseAgent(AgentInterface):
    """Base class for all agents."""

    def __init__(
            self,
            graph_source: Optional[Union[Graph, Pregel, Callable[[], Union[Graph, Pregel]]]] = None,
            config: Optional[AgentConfig] = None
    ):
        """Initialize BaseAgent.

        Args:
            agent_id:
            graph_source: Optional graph instance or function that returns a graph
            config: Optional configuration for the agent
        """
        self.agent_id = None
        self._graph = None
        self._compiled_graph = None
        self._graph_source = graph_source
        self._config = config

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

    def generate_agent_card(self, base_url: str) -> Optional[AgentCard]:
        """Generate the agent's capability card.

        Creates an AgentCard that describes this agent's capabilities,
        including its skills, supported input/output modes, and metadata.
        May be replaced with user's custom card.

        Args:
            base_url: Base URL where this agent is hosted, used to construct
                     the agent's endpoint URL.
        """
        return None

    def get_agent_card(self, base_url: str) -> Optional[AgentCard]:
        """Return the agent's card with capabilities description."""
        agent_card = self.generate_agent_card(base_url)
        if agent_card and isinstance(agent_card, AgentCard):
            return agent_card

        return self._generate_agent_card_from_config(base_url)

    def _generate_agent_card_from_config(self, base_url: str) -> AgentCard:
        """Generate agent card from config."""
        from a2a.types import AgentCapabilities, AgentSkill

        capabilities = AgentCapabilities(
            streaming=self.config.capabilities.streaming,
            push_notifications=self.config.capabilities.pushNotifications,
            extensions=[
                AgentExtension(
                    description="Get Conversation info based on context",
                    params=GetContextParams.model_json_schema(),
                    required=False,
                    uri=f"{aion_platform_settings.docs_url}/a2a/extensions/get-context"
                ),
                AgentExtension(
                    description="Get list of available contexts",
                    params=GetContextsListParams.model_json_schema(),
                    required=False,
                    uri=f"{aion_platform_settings.docs_url}/a2a/extensions/get-contexts"
                )
            ])

        skills = []
        for skill_config in self.config.skills:
            skill = AgentSkill(
                id=skill_config.id,
                name=skill_config.name,
                description=skill_config.description,
                tags=skill_config.tags,
                examples=skill_config.examples)
            skills.append(skill)

        return AionAgentCard(
            name=self.config.name or "Graph Agent",
            description=self.config.description or "Agent based on external graph",
            url=base_url,
            version=self.config.version or "1.0.0",
            defaultInputModes=self.config.input_modes,
            defaultOutputModes=self.config.output_modes,
            capabilities=capabilities,
            skills=skills,
            configuration=self.config.configuration
        )

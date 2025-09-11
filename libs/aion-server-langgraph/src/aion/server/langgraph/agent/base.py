import logging
from typing import Union, Optional, Callable

from a2a.types import AgentCard, AgentExtension, AgentCapabilities, AgentSkill
from langgraph.graph import Graph
from langgraph.pregel import Pregel

from .checkpointer import GraphCheckpointerManager
from .interfaces import AgentInterface
from .models import AgentConfig

from .card import AionAgentCard

from aion.server.types import GetContextParams, GetContextsListParams
from aion.server.configs import aion_platform_settings, app_settings
from aion.server.utils import substitute_vars
from aion.server.utils.constants import SPECIFIC_AGENT_RPC_PATH

logger = logging.getLogger(__name__)


class BaseAgent(AgentInterface):
    """Base class for all agents."""

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

        agent_url = "{base_url}/{rpc_path}".format(
            base_url=self.base_url,
            rpc_path=substitute_vars(
                template=SPECIFIC_AGENT_RPC_PATH,
                values={"graph_id": self.agent_id},
            ).lstrip("/")
        )

        return AionAgentCard(
            name=self.config.name or "Graph Agent",
            description=self.config.description or "Agent based on external graph",
            url=agent_url,
            version=self.config.version or "1.0.0",
            defaultInputModes=self.config.input_modes,
            defaultOutputModes=self.config.output_modes,
            capabilities=capabilities,
            skills=skills,
            configuration=self.config.configuration
        )

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

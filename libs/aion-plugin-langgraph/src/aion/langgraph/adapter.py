import inspect
from pathlib import Path
from typing import Any, Optional

from aion.shared.agent.adapters import AgentAdapter, ExecutorAdapter
from aion.shared.agent.exceptions import ConfigurationError
from aion.shared.config.models import AgentConfig
from aion.shared.db import DbManagerProtocol
from aion.shared.logging import get_logger
from langgraph.channels.ephemeral_value import EphemeralValue
from langgraph.channels.untracked_value import UntrackedValue
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.constants import START
from langgraph.graph import StateGraph
from langgraph.pregel import Pregel

from .checkpoint import CheckpointerFactory
from .constants import AION_UNTRACKED_CHANNEL_NAMES
from .execution import LangGraphExecutor

logger = get_logger()


class _FilteredEphemeralValue(EphemeralValue):
    """EphemeralValue that strips untracked keys from the input dict before checkpointing.

    Prevents SDK-managed fields (e.g. a2a_inbox) from being saved to the checkpoint
    via the __start__ channel, which stores the full input dict on every invocation.
    """

    def checkpoint(self):
        value = super().checkpoint()
        if isinstance(value, dict):
            return {k: v for k, v in value.items() if k not in AION_UNTRACKED_CHANNEL_NAMES}
        return value


class LangGraphAdapter(AgentAdapter):
    """LangGraph framework adapter with database dependency injection.

    This adapter handles LangGraph graph instances and provides execution
    capabilities. For PostgreSQL checkpointer support, a database manager
    instance should be provided via dependency injection.
    """

    def __init__(
            self,
            base_path: Optional[Path] = None,
            db_manager: Optional[DbManagerProtocol] = None
    ):
        """Initialize LangGraph adapter.

        Args:
            base_path: Base path for agent files (defaults to current directory)
            db_manager: Database manager instance for PostgreSQL checkpointer support.
                       If None, only memory checkpointers will be available.
        """
        self.base_path = base_path or Path.cwd()
        self._db_manager = db_manager

    @staticmethod
    def framework_name() -> str:
        return "langgraph"

    def get_supported_types(self) -> list[type]:
        """Return list of types this adapter can handle."""
        return [StateGraph, Pregel]

    def can_handle(self, agent_obj: Any) -> bool:
        if self._is_graph_instance(agent_obj):
            return True

        if callable(agent_obj) and not inspect.isclass(agent_obj):
            return True

        return False

    async def initialize_agent(self, agent_obj: Any, config: AgentConfig) -> Any:
        """Initialize agent from discovered object.

        Args:
            agent_obj: Graph instance or callable that returns a graph
            config: Agent configuration

        Returns:
            Compiled graph ready for execution
        """
        logger.debug(f"Initializing agent of type '{type(agent_obj).__name__}'")

        if self._is_graph_instance(agent_obj):
            return await self._compile_graph(agent_obj, config)

        if callable(agent_obj) and not inspect.isclass(agent_obj):
            logger.debug(f"Calling function to get graph")
            graph = agent_obj()
            if not self._is_graph_instance(graph):
                raise TypeError(
                    f"Function returned {type(graph).__name__}, expected a LangGraph graph"
                )
            return await self._compile_graph(graph, config)

        raise TypeError(
            f"Cannot initialize agent from object of type '{type(agent_obj).__name__}'. "
            f"Expected a LangGraph graph instance or a function that returns a graph."
        )

    async def create_executor(self, agent: Any, config: AgentConfig) -> ExecutorAdapter:
        """Create executor for the agent.

        Args:
            agent: Compiled graph from initialize_agent
            config: Agent configuration

        Returns:
            Executor adapter for running the agent
        """
        logger.debug(f"Creating executor for agent")

        if not self._is_graph_instance(agent):
            raise TypeError(
                f"Agent must be a compiled LangGraph graph, got {type(agent).__name__}"
            )

        return LangGraphExecutor(agent, config)

    def validate_config(self, config: AgentConfig) -> None:
        if not config.path:
            raise ConfigurationError("Agent path is required for LangGraph adapter")
        logger.debug(f"Configuration validated for agent by LangGraph adapter")

    async def _compile_graph(
            self,
            graph: Any,
            config: AgentConfig
    ) -> Any:
        """Compile a LangGraph graph with checkpointer.

        Args:
            graph: LangGraph graph instance
            config: Agent configuration

        Returns:
            Compiled graph
        """
        if isinstance(graph, Pregel) or graph.__class__.__name__ in {
            "CompiledStateGraph",
            "CompiledGraph",
            "CompiledMessageGraph"
        }:
            logger.debug(f"Graph is already compiled")
            return graph

        if hasattr(graph, "compile") and callable(getattr(graph, "compile")):
            logger.debug(f"Compiling graph")
            checkpointer = await self._get_checkpointer()
            self._patch_untracked_channels(graph)
            compiled_graph = graph.compile(checkpointer=checkpointer)
            self._patch_start_channel(compiled_graph)
            return compiled_graph
        else:
            logger.debug(f"Graph doesn't require compilation")
            return graph

    async def _get_checkpointer(self) -> Optional[BaseCheckpointSaver]:
        """Get checkpointer based on configuration.

        Uses PostgreSQL if available, otherwise falls back to in-memory.

        Returns:
            Checkpointer instance or None if creation fails
        """
        try:
            return await CheckpointerFactory.create(db_manager=self._db_manager)
        except Exception as ex:
            logger.warning(f"Failed to create checkpointer: {ex}")

    @staticmethod
    def _patch_untracked_channels(graph: StateGraph) -> None:
        """Replace channels for SDK-managed fields with UntrackedValue.

        Fields like a2a_inbox are re-injected on every invocation from the
        request context and must never be saved to checkpoints.
        """
        if not hasattr(graph, "channels"):
            return

        for name, channel in list(graph.channels.items()):
            if name not in AION_UNTRACKED_CHANNEL_NAMES:
                continue

            logger.debug(f"Marking channel '{name}' as untracked (not checkpointed)")
            graph.channels[name] = UntrackedValue(channel.ValueType)

    @staticmethod
    def _patch_start_channel(compiled_graph: Any) -> None:
        """Replace __start__ EphemeralValue with a filtered version.

        __start__ stores the full input dict on every invocation and checkpoints it.
        The filtered version strips untracked keys before storing, preventing
        SDK-managed fields from leaking into checkpoints via this channel.
        """
        if not hasattr(compiled_graph, "channels"):
            return

        start_channel = compiled_graph.channels.get(START)
        if not isinstance(start_channel, EphemeralValue):
            return

        filtered = _FilteredEphemeralValue(start_channel.typ)
        filtered.key = start_channel.key
        compiled_graph.channels[START] = filtered
        logger.debug(f"Patched __start__ channel to filter untracked keys")

    @staticmethod
    def _is_graph_instance(obj: Any) -> bool:
        """Check if object is a LangGraph graph instance.

        Args:
            obj: Object to check

        Returns:
            True if object is a supported graph type
        """
        if obj is None:
            return False

        if isinstance(obj, (StateGraph, Pregel)):
            return True
        return False

"""AgentAdapter implementation for LangGraph compiled graphs."""

import copy
import logging
import inspect
from pathlib import Path
from typing import Any, Optional

from aion.server.agent.adapters import AgentAdapter, AgentFrameworkExecutor
from aion.server.agent.exceptions import AdapterConfigurationError
from aion.core.config.models import AgentConfig
from aion.core.db import DbManagerProtocol
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import StateGraph
from langgraph.pregel import Pregel

from .checkpoint import CheckpointerFactory
from .checkpoint.files import linking_files
from .execution import LangGraphExecutor
from .outbox_channel import untracked_outbox_channels
from .store import OwnerScopedStore, StoreFactory

logger = logging.getLogger(__name__)


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
                       If None, an in-memory checkpointer is used. If an initialized
                       manager is given, PostgreSQL is required and a failure to
                       initialize it stops startup.
        """
        self.base_path = base_path or Path.cwd()
        self._db_manager = db_manager

    @staticmethod
    def framework_name() -> str:
        """Return the short framework identifier used for routing and logging."""
        return "langgraph"

    def get_supported_types(self) -> list[type]:
        """Return list of types this adapter can handle."""
        return [StateGraph, Pregel]

    def can_handle(self, agent_obj: Any) -> bool:
        """Return True for LangGraph graph instances and zero-arg factory callables, sync or async."""
        if self._is_graph_instance(agent_obj):
            return True

        if callable(agent_obj) and not inspect.isclass(agent_obj):
            return True

        return False

    async def initialize_agent(self, agent_obj: Any, config: AgentConfig) -> Any:
        """Initialize agent from discovered object.

        Args:
            agent_obj: Graph instance, or a callable that returns a graph. A
                callable that returns an awaitable - an ``async def``
                factory - is awaited on the server's event loop.
            config: Agent configuration

        Returns:
            Compiled graph ready for execution
        """
        logger.debug(f"Initializing agent of type '{type(agent_obj).__name__}'")

        if self._is_graph_instance(agent_obj):
            graph = agent_obj
        elif callable(agent_obj) and not inspect.isclass(agent_obj):
            logger.debug(f"Calling function to get graph")
            graph = agent_obj()
            if inspect.isawaitable(graph):
                graph = await graph
            if not self._is_graph_instance(graph):
                raise TypeError(
                    f"Function returned {type(graph).__name__}, expected a LangGraph graph"
                )
        else:
            raise TypeError(
                f"Cannot initialize agent from object of type '{type(agent_obj).__name__}'. "
                f"Expected a LangGraph graph instance or a function that returns a graph."
            )
        compiled = await self._compile_graph(graph, config)
        _note_input_without_messages(compiled)
        return compiled

    async def create_executor(self, agent: Any, config: AgentConfig) -> AgentFrameworkExecutor:
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
        """Raise AdapterConfigurationError if required LangGraph config fields are absent."""
        if not config.path:
            raise AdapterConfigurationError("Agent path is required for LangGraph adapter")
        logger.debug(f"Configuration validated for agent by LangGraph adapter")

    async def _compile_graph(
            self,
            graph: Any,
            config: AgentConfig
    ) -> Any:
        """Compile a LangGraph graph with a checkpointer and a store.

        A graph that arrives compiled - what ``langchain.agents.create_agent``
        returns - keeps a checkpointer it was compiled with, and is given the
        server's when it has none. The server cannot run a graph without one:
        every turn ends by reading the graph's state, which LangGraph refuses
        without a checkpointer, and memory between turns and ``interrupt()``
        need one too. A copy is returned; the caller's graph is not modified.
        ``checkpointer=False`` is an explicit choice and is left alone.
        A checkpointer of the graph's own is replaced in the copy by a copy
        of it that saves files as the server's do, as links to stored files
        (``checkpoint.files.linking_files``); its storage is the same.

        A graph without a store of its own - one the server compiles, or a
        compiled one without - is given the server's (``store``): memory
        across the user's conversations, kept apart for every agent and state
        owner. A store of the graph's own is left as it is.

        A state field that holds an ``A2AOutbox`` on LangGraph's ordinary
        channel is given the untracked channel ``A2AOutboxField`` declares,
        so the checkpoint holds no outbox (``outbox_channel``).

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
            update: dict[str, Any] = {}
            own = getattr(graph, "checkpointer", None)
            if own is None and hasattr(graph, "copy"):
                logger.debug("Graph is already compiled; attaching the server checkpointer")
                update["checkpointer"] = await self._get_checkpointer()
            else:
                logger.debug("Graph is already compiled with its own checkpointer")
                linked = linking_files(own)
                if linked is not own and hasattr(graph, "copy"):
                    update["checkpointer"] = linked
            if getattr(graph, "store", None) is None and hasattr(graph, "copy"):
                store = await self._get_store()
                if store is not None:
                    update["store"] = store
            channels = untracked_outbox_channels(graph)
            if channels is not None and hasattr(graph, "copy"):
                update["channels"] = channels
            return _copy_compiled(graph, update) if update else graph

        if hasattr(graph, "compile") and callable(getattr(graph, "compile")):
            logger.debug(f"Compiling graph")
            channels = untracked_outbox_channels(graph)
            if channels is not None:
                graph = copy.copy(graph)
                graph.channels = channels
            checkpointer = await self._get_checkpointer()
            compiled_graph = graph.compile(checkpointer=checkpointer, store=await self._get_store())
            return compiled_graph
        else:
            logger.debug(f"Graph doesn't require compilation")
            return graph

    async def _get_checkpointer(self) -> BaseCheckpointSaver:
        """Get checkpointer based on configuration.

        Returns:
            Checkpointer instance

        Raises:
            RuntimeError: A configured PostgreSQL checkpointer failed to
                initialize; startup stops instead of degrading to in-memory.
        """
        return await CheckpointerFactory.create(db_manager=self._db_manager)

    async def _get_store(self) -> Optional[OwnerScopedStore]:
        """The server's store: PostgreSQL with the checkpoints' database, memory without one.

        None where the installed langgraph-checkpoint-postgres cannot keep it
        on the server's pool (``store.StoreFactory``).
        """
        return await StoreFactory.create(db_manager=self._db_manager)

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


def _note_input_without_messages(graph: Any) -> None:
    """Log once, at startup, that a graph whose input has no ``messages`` field reads the message from its context.

    The server runs every turn with ``{"messages": [HumanMessage]}``, and
    LangGraph drops a key the graph's input does not declare. Such a graph
    is a valid one: it reads the turn's message with
    ``Thread.from_context(runtime.context).message``. An input schema that
    cannot be read, or is not an object of fields, says nothing either way.
    """
    input_jsonschema = getattr(graph, "get_input_jsonschema", None)
    if not callable(input_jsonschema):
        return
    try:
        schema = input_jsonschema()
    except Exception:
        logger.debug("The graph's input schema could not be read", exc_info=True)
        return
    fields = schema.get("properties") if isinstance(schema, dict) else None
    if isinstance(fields, dict) and "messages" not in fields:
        logger.info(
            "The graph's input has no 'messages' field, so the inbound message does not reach its state; "
            "the graph reads it from the turn's context: Thread.from_context(runtime.context).message."
        )


def _copy_compiled(graph: Any, update: dict[str, Any]) -> Any:
    """``graph.copy(update)``, keeping the msgpack allowlist LangGraph built from the graph's schema.

    ``Pregel.copy`` builds a new graph from the old one's attributes, and the
    allowlist, which LangGraph builds when it compiles a graph and applies to
    the checkpointer in its strict msgpack mode, is not one it takes over:
    without it, a type the state schema declares would load as a dict in
    that mode.
    """
    copied = graph.copy(update=update)
    allowlist = getattr(graph, "_serde_allowlist", None)
    if allowlist is not None and getattr(copied, "_serde_allowlist", None) is None:
        copied._serde_allowlist = allowlist
    return copied

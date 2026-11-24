"""AgentAdapter implementation for Google ADK agents."""

import logging
import inspect
from pathlib import Path
from typing import Any, Optional, Union

from aion.server.agent.adapters import AgentAdapter, AgentFrameworkExecutor
from aion.server.agent.exceptions import AdapterConfigurationError
from aion.core.config.models import AgentConfig
from aion.core.db import DbManagerProtocol
from google.adk.agents import BaseAgent
from google.adk.apps import App

from .artifacts.factory import ArtifactServiceFactory
from .execution import ADKExecutor
from .memory import MemoryServiceFactory
from .session import SessionServiceFactory

try:  # ADK 2.x: an agent is one kind of node, a Workflow another
    from google.adk.workflow import BaseNode
except ImportError:  # ADK 1.x has no nodes besides agents
    BaseNode = None

logger = logging.getLogger(__name__)

_NODE_TYPES: tuple[type, ...] = (BaseNode,) if BaseNode is not None else ()
"""ADK's node type, where the installed ADK has one: what an ``App``'s ``root_agent`` may be in ADK 2.x."""


class ADKAdapter(AgentAdapter):
    """AgentAdapter implementation that bridges Aion server with Google ADK agents.

    The server runs an ADK agent (``BaseAgent``), given as it is or held by
    an ADK ``App``, whose plugins, events compaction and context cache then
    run with it. A ``Workflow``, or any other ADK node that is not an agent,
    is refused at startup.
    """

    def __init__(
            self,
            base_path: Optional[Path] = None,
            db_manager: Optional[DbManagerProtocol] = None,
    ):
        """Initialize ADK adapter.

        Args:
            base_path: Base path for agent files (defaults to current directory)
            db_manager: Database manager instance for DatabaseSessionService support.
                       If None, InMemorySessionService is used. If an initialized
                       manager is given, PostgreSQL is required and a failure to
                       initialize it stops startup.
        """
        self.base_path = base_path or Path.cwd()
        self.db_manager = db_manager

    @staticmethod
    def framework_name() -> str:
        """Return the short framework identifier used for routing and logging."""
        return "adk"

    def get_supported_types(self) -> list[type]:
        """Return the ADK types this adapter takes from a module: an App, an agent, and ADK 2.x's other nodes.

        A node that is not an agent is taken only to be refused with a
        message that names it.
        """
        return [App, BaseAgent, *_NODE_TYPES]

    def get_preferred_types(self) -> list[type]:
        """An App first, as ADK's own loader takes ``app`` before ``root_agent``, then an agent."""
        return [App, BaseAgent]

    def can_handle(self, agent_obj: Any) -> bool:
        """Return True for an ADK App, agent or node and for zero-arg factory callables, sync or async."""
        if self._is_adk_object(agent_obj):
            return True

        # Check callable that might return an agent
        if callable(agent_obj) and not inspect.isclass(agent_obj):
            return True

        return False

    async def initialize_agent(self, agent_obj: Any, config: AgentConfig) -> Union[BaseAgent, App]:
        """Resolve agent_obj to what the server runs: an ADK agent, or an ADK App that holds one.

        A factory is called, and one that returns an awaitable - an
        ``async def`` factory - is awaited on the server's event loop.

        Raises:
            TypeError: The object, or what its factory returned, is neither
                an ADK agent nor an App; it is a ``Workflow`` or another ADK
                node that is not an agent; or it is an App whose
                ``root_agent`` is one.
        """
        logger.debug(f"Initializing ADK agent of type '{type(agent_obj).__name__}'")

        root = agent_obj
        if not self._is_adk_object(root):
            if not callable(root) or inspect.isclass(root):
                raise TypeError(
                    f"Cannot initialize agent from object of type '{type(root).__name__}'. "
                    f"Expected an ADK agent, an ADK App, or a callable that returns one."
                )
            logger.debug("Calling factory function to get ADK agent")
            root = root()
            if inspect.isawaitable(root):
                root = await root
            if not self._is_adk_object(root):
                raise TypeError(
                    f"Factory function returned {type(root).__name__}, "
                    f"expected an ADK agent or an ADK App"
                )
        return _checked_root(root)

    async def create_executor(self, agent: Any, config: AgentConfig) -> AgentFrameworkExecutor:
        """Build and return an ADKExecutor wired with session and artifact services."""
        logger.debug("Creating executor for ADK agent")

        if not isinstance(agent, (BaseAgent, App)):
            raise TypeError(
                f"Agent must be an ADK agent or an ADK App, got {type(agent).__name__}"
            )

        session_service = await SessionServiceFactory.create(db_manager=self.db_manager)
        artifact_service = ArtifactServiceFactory.create(db_manager=self.db_manager)
        memory_service = MemoryServiceFactory.create(db_manager=self.db_manager)
        return ADKExecutor(
            agent,
            config,
            session_service=session_service,
            artifact_service=artifact_service,
            memory_service=memory_service,
        )

    def validate_config(self, config: AgentConfig) -> None:
        """Raise AdapterConfigurationError if required ADK config fields are absent."""
        if not config.path:
            raise AdapterConfigurationError("Agent path is required for ADK adapter")
        logger.debug(f"Configuration validated for agent by ADK adapter")

    @staticmethod
    def _is_adk_object(obj: Any) -> bool:
        """Return True if obj is an ADK App, agent or node."""
        return isinstance(obj, (App, BaseAgent, *_NODE_TYPES))


def _checked_root(root: Any) -> Union[BaseAgent, App]:
    """``root`` if the server can run it: an ADK agent, or an App whose ``root_agent`` is one.

    An App that turns resumability off is run resumable all the same, with
    a warning: without it a long-running tool call does not pause the run,
    and the user is never asked.

    Raises:
        TypeError: ``root`` is a ``Workflow`` or another ADK node that is
            not an agent, or an App whose ``root_agent`` is one.
    """
    if isinstance(root, App):
        if not isinstance(root.root_agent, BaseAgent):
            raise TypeError(
                f"The root_agent of the ADK App '{root.name}' is {type(root.root_agent).__name__}: "
                f"the server runs an ADK agent (BaseAgent), and an ADK Workflow is not supported"
            )
        if root.resumability_config is not None and not root.resumability_config.is_resumable:
            logger.warning(
                "The ADK App '%s' sets resumability_config.is_resumable to False; the server runs every "
                "invocation resumable, so a long-running tool call pauses the run and the reply resumes it",
                root.name,
            )
        return root
    if isinstance(root, BaseAgent):
        return root
    raise TypeError(
        f"An ADK {type(root).__name__} cannot be the agent: the server runs an ADK agent (BaseAgent), "
        f"or an ADK App whose root_agent is one, and an ADK Workflow is not supported"
    )

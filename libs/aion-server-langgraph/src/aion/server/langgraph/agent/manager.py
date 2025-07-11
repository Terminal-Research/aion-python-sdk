from __future__ import annotations

import os
import logging
import importlib
import importlib.util
import inspect
import yaml
from pathlib import Path
from types import ModuleType
from typing import Dict, Any, Optional, Union

from aion.server.utils.metaclasses import Singleton
from .base import BaseAgent

logger = logging.getLogger(__name__)

# LangGraph is optional in this environment. Define minimal stubs if the package
# is not installed so type checks and isinstance comparisons do not fail at
# runtime when LangGraph is available.
try:  # pragma: no cover - optional dependency
    from langgraph.graph import Graph
    from langgraph.pregel import Pregel
except Exception:  # pragma: no cover - local testing without dependency
    from aion.server.langgraph.graph.langgraph_interfaces import Graph, Pregel


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

    def _import_module(self, module_str: str, base_dir: Path) -> ModuleType:
        """Import a module from a dotted path or a file path.

        Args:
            module_str: Module path string (dotted notation or file path).
            base_dir: Base directory for resolving relative paths.

        Returns:
            Imported module object.

        Raises:
            FileNotFoundError: If the module file is not found.
            ValueError: If the module cannot be loaded.
        """
        if module_str.endswith(".py") or "/" in module_str or module_str.startswith("."):
            path = (base_dir / module_str).resolve()
            if not path.exists():
                raise FileNotFoundError(f"Agent module not found: {path}")

            mod_name = path.stem + "_agent"
            spec = importlib.util.spec_from_file_location(mod_name, path)
            if spec is None or spec.loader is None:
                raise ValueError(f"Could not load module from path: {path}")

            logger.debug("Importing module from %s", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)  # type: ignore[arg-type]
        else:
            logger.debug("Importing module '%s'", module_str)
            module = importlib.import_module(module_str)
        return module

    def _load_agent(self, import_str: str, base_dir: Path) -> BaseAgent:
        """Load an agent object from an import string.

        Args:
            import_str: Import string in format 'module:ClassName' or just 'module'.
            base_dir: Base directory for resolving relative module paths.

        Returns:
            Loaded agent instance.

        Raises:
            ValueError: If the agent cannot be found or loaded.
            TypeError: If the loaded object is not a BaseAgent subclass.
        """
        module_part, _, class_part = import_str.partition(":")
        module = self._import_module(module_part, base_dir)
        logger.debug("Loaded module '%s' for agent", module_part)

        if class_part:
            # Explicit class name provided
            if class_part not in module.__dict__:
                raise ValueError(
                    f"Could not find agent class '{class_part}' in module '{module_part}'"
                )
            agent_class = module.__dict__[class_part]
            logger.debug("Found class '%s' in module '%s'", class_part, module_part)
        else:
            # Auto-discover BaseAgent subclass
            agent_class = None
            for _, member in inspect.getmembers(module, inspect.isclass):
                if (issubclass(member, BaseAgent) and
                        member is not BaseAgent and
                        member.__module__ == module.__name__):
                    agent_class = member
                    logger.debug(
                        "Discovered BaseAgent subclass '%s' in module '%s'",
                        member.__name__, module_part
                    )
                    break

            if agent_class is None:
                raise ValueError(f"No BaseAgent subclass found in module '{module_part}'")

        # Validate that it's actually a BaseAgent subclass
        if not (inspect.isclass(agent_class) and issubclass(agent_class, BaseAgent)):
            raise TypeError(
                f"'{class_part or agent_class.__name__}' in module '{module_part}' "
                f"must be a subclass of BaseAgent"
            )

        # Instantiate the agent
        try:
            logger.debug("Instantiating agent class '%s'", agent_class.__name__)
            agent_instance = agent_class()
        except Exception as e:
            raise ValueError(
                f"Failed to instantiate agent class '{agent_class.__name__}': {e}"
            ) from e

        return agent_instance

    def _load_simple_yaml(self, path: Path) -> Dict[str, Any]:
        """Load a YAML file using PyYAML.

        Args:
            path: Location of the YAML file.

        Returns:
            Parsed mapping.
        """
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)

        if not isinstance(data, dict):
            raise ValueError("Configuration file must contain a mapping at the root")

        return data

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
        path = Path(config_path)
        if not path.is_absolute():
            path = Path(os.getcwd()) / path

        logger.info("Loading agents from %s", path)
        config = self._load_simple_yaml(path)

        aion_cfg = config.get("aion", {})
        agents_cfg = aion_cfg.get("agent", {}) or aion_cfg.get("graph", {})
        if not agents_cfg:
            logger.warning("No agents configured in %s", path)
            return

        base_dir = path.parent
        for agent_id, import_str in agents_cfg.items():
            logger.info("Importing agent '%s' from '%s'", agent_id, import_str)
            try:
                agent_instance = self._load_agent(import_str, base_dir)
                self.set_agent(agent_id=agent_id, agent=agent_instance)
            except Exception as e:
                logger.error(f"Failed to load agent '{agent_id}': {e}")
                raise

        self.precompile_all()
        logger.info("Initialized %d agents", len(agents_cfg))

    def has_active_agents(self) -> bool:
        """Check if any agents are loaded."""
        return bool(self.agents)


# Global instance
agent_manager = AgentManager()

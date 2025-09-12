from __future__ import annotations

import importlib
import importlib.util
import inspect
import logging
from pathlib import Path
from types import ModuleType
from typing import Dict, Any, Optional, Union, Callable

from aion.shared.aion_config import AionConfigReader
from aion.shared.utils import get_config_path
from .base import BaseAgent
from aion.shared.aion_config import AgentConfig, AgentCapabilities

logger = logging.getLogger(__name__)

# LangGraph imports with fallback
from langgraph.graph import Graph
from langgraph.pregel import Pregel


class AgentFactory:
    """Handles creation and instantiation of agents based on configuration."""

    def __init__(self, config_path: Optional[Path] = None, logger_: logging.Logger | None = None):
        self.config_path = config_path or get_config_path()
        self.logger = logger_ or logging.getLogger(__name__)

    def import_module(self, module_str: str) -> ModuleType:
        """Import a module from a dotted path or a file path.

        Args:
            module_str: Module path string (dotted notation or file path).

        Returns:
            Imported module object.

        Raises:
            FileNotFoundError: If the module file is not found.
            ValueError: If the module cannot be loaded.
        """
        if module_str.endswith(".py") or "/" in module_str or module_str.startswith("."):
            path = (self.config_path.parent / module_str).resolve()
            if not path.exists():
                raise FileNotFoundError(f"Module not found: {path}")

            mod_name = path.stem + "_module"
            spec = importlib.util.spec_from_file_location(mod_name, path)
            if spec is None or spec.loader is None:
                raise ValueError(f"Could not load module from path: {path}")

            self.logger.debug("Importing module from %s", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        else:
            self.logger.debug("Importing module '%s'", module_str)
            module = importlib.import_module(module_str)

        return module

    @staticmethod
    def _is_graph_instance(obj: Any) -> bool:
        """Check if an object is a graph instance.

        Args:
            obj: Object to check.

        Returns:
            True if the object is a graph instance, False otherwise.
        """
        if obj is None:
            return False

        # Check for LangGraph types
        if isinstance(obj, (Graph, Pregel)):
            return True

        # Check by class name for common graph types
        class_name = obj.__class__.__name__
        graph_class_names = {
            'StateGraph', 'CompiledStateGraph', 'CompiledGraph',
            'MessageGraph', 'CompiledMessageGraph'
        }

        return class_name in graph_class_names

    @staticmethod
    def _is_graph_function(obj: Any) -> bool:
        """Check if an object is a function that returns a graph.

        Args:
            obj: Object to check.

        Returns:
            True if the object is a function, False otherwise.
        """
        if not callable(obj):
            return False

        # Exclude classes, methods, and built-in functions
        if inspect.isclass(obj) or inspect.ismethod(obj) or inspect.isbuiltin(obj):
            return False

        # Exclude typing constructs like Annotated, Union, etc.
        if hasattr(obj, '__module__') and obj.__module__ == 'typing':
            return False

        # Exclude typing_extensions constructs
        if hasattr(obj, '__module__') and obj.__module__ == 'typing_extensions':
            return False

        # Only consider regular functions and lambdas
        return inspect.isfunction(obj) or (callable(obj) and hasattr(obj, '__call__'))

    def discover_agent_item(self, module: ModuleType, item_name: Optional[str] = None) -> Union[type, object, Callable]:
        """Discover an agent class, graph instance, or graph function in a module.

        Args:
            module: Module to search in.
            item_name: Specific item name to look for, or None for auto-discovery.

        Returns:
            Found class, graph instance, or graph function.

        Raises:
            ValueError: If no suitable item is found.
        """
        if item_name:
            # Explicit item name provided
            if item_name not in module.__dict__:
                raise ValueError(f"Item '{item_name}' not found in module '{module.__name__}'")
            return module.__dict__[item_name]

        # Auto-discovery with priority order:
        # 1. BaseAgent subclass
        # 2. Graph instance

        # First, try to find BaseAgent subclass
        for _, member in inspect.getmembers(module, inspect.isclass):
            if (
                    issubclass(member, BaseAgent) and
                    member is not BaseAgent and
                    member.__module__ == module.__name__
            ):
                self.logger.debug(f"Found BaseAgent subclass '{member.__name__}' in module '{module.__name__}'")
                return member

        # Then, look for graph instances
        for name, member in inspect.getmembers(module):
            if self._is_graph_instance(member):
                self.logger.debug(
                    f"Found graph instance '{name}' of type '{type(member).__name__}' in module '{module.__name__}'")
                return member

        raise ValueError(
            f"No BaseAgent subclass or graph instance found in module '{module.__name__}'")

    def load_from_path(self, path: str) -> BaseAgent:
        """Load an agent from a path.

        Args:
            path: Import path string.

        Returns:
            BaseAgent instance.

        Raises:
            ValueError: If the item cannot be loaded.
            TypeError: If the item is not a valid agent class, graph instance, or graph function.
        """
        module_part, _, item_part = path.partition(":")
        module = self.import_module(module_part)

        item = self.discover_agent_item(module, item_part if item_part else None)

        # Handle different types of discovered items
        if inspect.isclass(item):
            # It's a class - check if it's a BaseAgent subclass
            if issubclass(item, BaseAgent):
                try:
                    return item()  # Instantiate the agent
                except Exception as e:
                    raise ValueError(f"Failed to instantiate agent class '{item.__name__}': {e}") from e
            else:
                raise TypeError(f"Class '{item.__name__}' must be a subclass of BaseAgent")

        elif self._is_graph_function(item):
            # It's a function that should return a graph
            try:
                # Test if the function returns a graph
                test_result = item()
                if not self._is_graph_instance(test_result):
                    function_name = getattr(item, '__name__', str(item))
                    raise TypeError(f"Function '{function_name}' must return a graph instance, got {type(test_result)}")

                # Create BaseAgent with the function as a graph source
                return BaseAgent(graph_source=item)
            except TypeError as e:
                # Re-raise TypeError as-is
                raise e
            except Exception as e:
                function_name = getattr(item, '__name__', str(item))
                raise ValueError(f"Failed to create agent from function '{function_name}': {e}") from e

        elif self._is_graph_instance(item):
            # It's a graph instance - create BaseAgent with it
            return BaseAgent(graph_source=item)

        else:
            raise TypeError(f"Item must be a BaseAgent class, graph function, or graph instance, got {type(item)}")

    @staticmethod
    def create_minimal_config(agent_id: str, item_type: str = "graph") -> AgentConfig:
        """Create a minimal configuration for an agent.

        Args:
            agent_id: Agent identifier.
            item_type: Type of item ("graph", "function", or "class").

        Returns:
            Minimal agent configuration.
        """
        return AgentConfig(
            path="",  # Will be set by caller
            name=f'{agent_id.replace("-", " ").title()} Agent',
            description=f'Agent created from {item_type}',
            version='1.0.0',
            capabilities=AgentCapabilities(),
            skills=[],
            input_modes=['text'],
            output_modes=['text']
        )

    def create_agent(
            self, agent_id: str, config: Union[str, Dict[str, Any]],
            config_reader: AionConfigReader
    ) -> BaseAgent:
        """Create a single agent instance from the configuration.

        Args:
            agent_id: Agent identifier.
            config: Agent configuration (string path or config dict).
            config_reader: Config reader instance for parsing complex configs.

        Returns:
            BaseAgent instance.

        Raises:
            ValueError: If the configuration is invalid.
            TypeError: If the loaded item is not valid.
        """
        # If it's a string, treat it as a path (backward compatibility)
        if isinstance(config, str):
            agent = self.load_from_path(config)
            agent.agent_id = agent_id

            # If the agent was created from a graph source, add minimal config
            if not agent.config:
                minimal_config = self.create_minimal_config(agent_id, "auto-discovered")
                agent.config = minimal_config

            return agent

        # If it's a dict, parse the configuration
        if not isinstance(config, dict):
            raise ValueError(f"Agent config must be string or dict, got {type(config)}")

        # Must have 'path' specified
        if 'path' not in config:
            raise ValueError(f"Agent config must specify 'path'")

        # Parse and validate the configuration
        try:
            agent_config = config_reader.parse_agent_config(config)
        except ValueError as e:
            raise ValueError(f"Invalid configuration for agent '{agent_id}': {e}") from e

        agent = self.load_from_path(agent_config.path)
        agent.agent_id = agent_id

        # Set the validated config on the agent
        agent.config = agent_config

        return agent

    def create_all_agents(
            self, agents_config: Dict[str, Union[str, Dict[str, Any]]],
            config_reader: AionConfigReader
    ) -> Dict[str, BaseAgent]:
        """Create all agent instances from configurations.

        Args:
            agents_config: Dictionary mapping agent IDs to configurations.
            config_reader: Config reader instance for parsing complex configs.

        Returns:
            Dictionary mapping agent IDs to BaseAgent instances.

        Raises:
            ValueError: If any agent configuration is invalid.
        """
        agents = {}

        for agent_id, agent_config in agents_config.items():
            self.logger.info("Creating agent '%s'", agent_id)
            try:
                agent_instance = self.create_agent(agent_id, agent_config, config_reader)
                agents[agent_id] = agent_instance
                self.logger.info("Successfully created agent '%s'", agent_id)
            except Exception as e:
                self.logger.error(f"Failed to create agent '{agent_id}': {e}")
                raise

        return agents

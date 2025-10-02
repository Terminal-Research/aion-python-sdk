from __future__ import annotations

import importlib
import importlib.util
import inspect
import logging
from pathlib import Path
from types import ModuleType
from typing import Optional, Union, Callable, Any

from aion.shared.aion_config import AgentConfig
from aion.shared.logging import get_logger

from .base import BaseAgent

# LangGraph imports with fallback
from langgraph.graph import Graph
from langgraph.pregel import Pregel


class AgentFactory:
    """Factory for creating a single BaseAgent instance directly from AgentConfig object."""

    def __init__(self, base_path: Optional[Path] = None, logger_: Optional[logging.Logger] = None):
        """Initialize factory with optional base path for relative imports.

        Args:
            base_path: Base path for resolving relative module paths
            logger_: Logger instance to use
        """
        self.base_path = base_path or Path.cwd()
        self.logger = logger_ or get_logger("AgentFactory")

    def create_agent_from_config(self, agent_id: str, agent_config: AgentConfig) -> BaseAgent:
        """Create a BaseAgent instance directly from an AgentConfig object.

        Args:
            agent_id: Unique identifier for the agent
            agent_config: Complete agent configuration object

        Returns:
            BaseAgent instance with the provided configuration

        Raises:
            ValueError: If the agent cannot be created from the configuration
            TypeError: If the loaded item is not a valid agent type
        """
        self.logger.info("Creating agent '%s' from config", agent_id)

        try:
            # Load the agent item from the configured path
            agent_item = self._load_from_path(agent_config.path)

            # Create BaseAgent instance
            agent = self._create_base_agent(agent_item)

            # Set agent properties
            agent.agent_id = agent_id
            agent.config = agent_config

            self.logger.info("Successfully created agent '%s'", agent_id)
            return agent

        except Exception as e:
            self.logger.error("Failed to create agent '%s': %s", agent_id, e)
            raise

    def _import_module(self, module_str: str) -> ModuleType:
        """Import a module from a dotted path or a file path.

        Args:
            module_str: Module path string (dotted notation or file path)

        Returns:
            Imported module object

        Raises:
            FileNotFoundError: If the module file is not found
            ValueError: If the module cannot be loaded
        """
        if module_str.endswith(".py") or "/" in module_str or module_str.startswith("."):
            # File path import
            path = (self.base_path / module_str).resolve()
            if not path.exists():
                raise FileNotFoundError(f"Module not found: {path}")

            mod_name = path.stem + "_module"
            spec = importlib.util.spec_from_file_location(mod_name, path)
            if spec is None or spec.loader is None:
                raise ValueError(f"Could not load module from path: {path}")

            self.logger.info("Importing module from %s", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        else:
            # Dotted path import
            self.logger.info("Importing module '%s'", module_str)
            module = importlib.import_module(module_str)

        return module

    @staticmethod
    def _is_graph_instance(obj: Any) -> bool:
        """Check if an object is a graph instance.

        Args:
            obj: Object to check

        Returns:
            True if the object is a graph instance, False otherwise
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
            obj: Object to check

        Returns:
            True if the object is a function, False otherwise
        """
        if not callable(obj):
            return False

        # Exclude classes, methods, and built-in functions
        if inspect.isclass(obj) or inspect.ismethod(obj) or inspect.isbuiltin(obj):
            return False

        # Exclude typing constructs
        if hasattr(obj, '__module__') and obj.__module__ in ('typing', 'typing_extensions'):
            return False

        # Only consider regular functions and lambdas
        return inspect.isfunction(obj) or (callable(obj) and hasattr(obj, '__call__'))

    def _discover_agent_item(self, module: ModuleType, item_name: Optional[str] = None) -> Union[
        type, object, Callable]:
        """Discover an agent class, graph instance, or graph function in a module.

        Args:
            module: Module to search in
            item_name: Specific item name to look for, or None for auto-discovery

        Returns:
            Found class, graph instance, or graph function

        Raises:
            ValueError: If no suitable item is found
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
                self.logger.info("Found BaseAgent subclass '%s' in module '%s'",
                                 member.__name__, module.__name__)
                return member

        # Then, look for graph instances
        for name, member in inspect.getmembers(module):
            if self._is_graph_instance(member):
                self.logger.info("Found graph instance '%s' of type '%s' in module '%s'",
                                 name, type(member).__name__, module.__name__)
                return member

        raise ValueError(f"No BaseAgent subclass or graph instance found in module '{module.__name__}'")

    def _load_from_path(self, path: str) -> Union[type, object, Callable]:
        """Load an agent item from a path.

        Args:
            path: Import path string (module:item or just module)

        Returns:
            Agent class, graph instance, or graph function

        Raises:
            ValueError: If the item cannot be loaded
        """
        module_part, _, item_part = path.partition(":")
        module = self._import_module(module_part)

        return self._discover_agent_item(module, item_part if item_part else None)

    def _create_base_agent(self, agent_item: Union[type, object, Callable]) -> BaseAgent:
        """Create BaseAgent instance from discovered agent item.

        Args:
            agent_item: Agent class, graph instance, or graph function

        Returns:
            BaseAgent instance

        Raises:
            TypeError: If the item is not a valid agent type
            ValueError: If the item cannot be instantiated
        """
        if inspect.isclass(agent_item):
            # It's a class - check if it's a BaseAgent subclass
            if issubclass(agent_item, BaseAgent):
                try:
                    return agent_item()  # Instantiate the agent
                except Exception as e:
                    raise ValueError(f"Failed to instantiate agent class '{agent_item.__name__}': {e}") from e
            else:
                raise TypeError(f"Class '{agent_item.__name__}' must be a subclass of BaseAgent")

        elif self._is_graph_function(agent_item):
            # It's a function that should return a graph
            try:
                # Test if the function returns a graph
                test_result = agent_item()
                if not self._is_graph_instance(test_result):
                    function_name = getattr(agent_item, '__name__', str(agent_item))
                    raise TypeError(f"Function '{function_name}' must return a graph instance, got {type(test_result)}")

                # Create BaseAgent with the function as a graph source
                return BaseAgent(graph_source=agent_item)
            except TypeError as e:
                # Re-raise TypeError as-is
                raise e
            except Exception as e:
                function_name = getattr(agent_item, '__name__', str(agent_item))
                raise ValueError(f"Failed to create agent from function '{function_name}': {e}") from e

        elif self._is_graph_instance(agent_item):
            # It's a graph instance - create BaseAgent with it
            return BaseAgent(graph_source=agent_item)

        else:
            raise TypeError(
                f"Item must be a BaseAgent class, graph function, or graph instance, got {type(agent_item)}")

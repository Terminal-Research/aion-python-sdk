import importlib
import importlib.util
import inspect
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Optional, Union

from aion.shared.aion_config.models import AgentConfig
from aion.shared.logging import get_logger
from langgraph.graph import Graph
from langgraph.pregel import Pregel

from aion.server.adapters.base.agent_adapter import AgentAdapter
from aion.server.adapters.base.executor_adapter import ExecutorAdapter
from aion.server.adapters.exceptions import ConfigurationError
from aion.server.adapters.langgraph.executor import LangGraphExecutor
from aion.server.langgraph.agent.base import BaseAgent

logger = get_logger()

class LangGraphAdapter(AgentAdapter):

    def __init__(self, base_path: Optional[Path] = None):
        self.base_path = base_path or Path.cwd()

    @property
    def framework_name(self) -> str:
        return "langgraph"

    def can_handle(self, agent_obj: Any) -> bool:
        if isinstance(agent_obj, BaseAgent):
            return True
        if inspect.isclass(agent_obj) and issubclass(agent_obj, BaseAgent):
            return True
        if isinstance(agent_obj, (Graph, Pregel)):
            return True
        class_name = agent_obj.__class__.__name__
        graph_class_names = {
            "StateGraph",
            "CompiledStateGraph",
            "CompiledGraph",
            "MessageGraph",
            "CompiledMessageGraph",
        }

        if class_name in graph_class_names:
            return True
        if callable(agent_obj) and not inspect.isclass(agent_obj):
            return True

        return False

    def discover_agent(self, module: Any, config: AgentConfig) -> Any:
        logger.debug(f"Discovering agent in module '{module.__name__}'")
        for name, member in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(member, BaseAgent)
                and member is not BaseAgent
                and member.__module__ == module.__name__
            ):
                logger.debug(
                    f"Found BaseAgent subclass '{member.__name__}' in module '{module.__name__}'"
                )
                return member
        for name, member in inspect.getmembers(module):
            if self._is_graph_instance(member):
                logger.debug(
                    f"Found graph instance '{name}' of type '{type(member).__name__}' in module '{module.__name__}'"
                )
                return member
        for name, member in inspect.getmembers(module, inspect.isfunction):
            if not name.startswith("_"):
                logger.debug(f"Found potential graph function '{name}'")
                return member

        raise ValueError(
            f"No BaseAgent subclass, graph instance, or graph function found in module '{module.__name__}'"
        )

    def initialize_agent(self, agent_obj: Any, config: AgentConfig) -> Any:
        logger.debug(f"Initializing agent of type '{type(agent_obj).__name__}'")
        if inspect.isclass(agent_obj) and issubclass(agent_obj, BaseAgent):
            agent_instance = agent_obj()
            agent_instance.config = config
            return agent_instance
        if isinstance(agent_obj, BaseAgent):
            agent_obj.config = config
            return agent_obj
        if self._is_graph_instance(agent_obj) or callable(agent_obj):
            agent_instance = BaseAgent(graph_source=agent_obj, config=config)
            return agent_instance

        raise TypeError(
            f"Cannot initialize agent from object of type '{type(agent_obj).__name__}'"
        )

    def create_executor(self, agent: Any, config: AgentConfig) -> ExecutorAdapter:
        if not isinstance(agent, BaseAgent):
            raise TypeError(
                f"Agent must be a BaseAgent instance, got {type(agent).__name__}"
            )

        logger.debug(f"Creating executor for agent '{config.id}'")
        compiled_graph = agent.get_compiled_graph()

        return LangGraphExecutor(compiled_graph, config)

    def validate_config(self, config: AgentConfig) -> None:
        if not config.path:
            raise ConfigurationError("Agent path is required for LangGraph adapter")
        logger.debug(f"Configuration validated for agent '{config.id}'")

    def get_metadata(self, agent: Any) -> dict[str, Any]:
        metadata = {
            "framework": self.framework_name,
        }

        if isinstance(agent, BaseAgent):
            metadata["agent_class"] = agent.__class__.__name__
            if hasattr(agent, "card"):
                metadata["card"] = {
                    "name": agent.card.name,
                    "description": agent.card.description,
                }

        return metadata

    def _import_module(self, module_str: str) -> ModuleType:
        if (
            module_str.endswith(".py")
            or "/" in module_str
            or module_str.startswith(".")
        ):
            path = (self.base_path / module_str).resolve()
            if not path.exists():
                raise FileNotFoundError(f"Module not found: {path}")

            mod_name = path.stem + "_module"
            spec = importlib.util.spec_from_file_location(mod_name, path)
            if spec is None or spec.loader is None:
                raise ValueError(f"Could not load module from path: {path}")

            logger.debug(f"Importing module from {path}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        else:
            logger.debug(f"Importing module '{module_str}'")
            module = importlib.import_module(module_str)

        return module

    @staticmethod
    def _is_graph_instance(obj: Any) -> bool:
        if obj is None:
            return False
        if isinstance(obj, (Graph, Pregel)):
            return True
        class_name = obj.__class__.__name__
        graph_class_names = {
            "StateGraph",
            "CompiledStateGraph",
            "CompiledGraph",
            "MessageGraph",
            "CompiledMessageGraph",
        }

        return class_name in graph_class_names



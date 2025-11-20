import inspect
from pathlib import Path
from typing import Any, Optional

from aion.shared.agent import AgentAdapter, ExecutorAdapter, ConfigurationError
from aion.shared.config.models import AgentConfig
from aion.shared.logging import get_logger
from google.adk.agents import Agent as BaseAgent

from .executor import ADKExecutor

logger = get_logger()


class ADKAdapter(AgentAdapter):
    def __init__(self, base_path: Optional[Path] = None):
        self.base_path = base_path or Path.cwd()

    @staticmethod
    def framework_name() -> str:
        return "adk"

    def get_supported_types(self) -> list[type]:
        return [BaseAgent]

    def get_supported_type_names(self) -> set[str]:
        return {
            "Agent",
            "LlmAgent",
            "SequentialAgent",
            "ParallelAgent",
            "LoopAgent",
        }

    def can_handle(self, agent_obj: Any) -> bool:
        # Check direct instance
        if self._is_adk_agent_instance(agent_obj):
            return True

        # Check callable that might return an agent
        if callable(agent_obj) and not inspect.isclass(agent_obj):
            return True

        return False

    async def initialize_agent(self, agent_obj: Any, config: AgentConfig) -> Any:
        logger.debug(f"Initializing ADK agent of type '{type(agent_obj).__name__}'")

        # Direct agent instance - return as-is
        if self._is_adk_agent_instance(agent_obj):
            logger.debug("Agent is already an ADK instance")
            return agent_obj

        # Callable factory function
        if callable(agent_obj) and not inspect.isclass(agent_obj):
            logger.debug("Calling factory function to get ADK agent")
            agent = agent_obj()
            if not self._is_adk_agent_instance(agent):
                raise TypeError(
                    f"Factory function returned {type(agent).__name__}, "
                    f"expected an ADK agent"
                )
            return agent

        raise TypeError(
            f"Cannot initialize agent from object of type '{type(agent_obj).__name__}'. "
            f"Expected an ADK agent instance or a callable that returns an agent."
        )

    async def create_executor(self, agent: Any, config: AgentConfig) -> ExecutorAdapter:
        logger.debug("Creating executor for ADK agent")

        if not self._is_adk_agent_instance(agent):
            raise TypeError(
                f"Agent must be an ADK agent instance, got {type(agent).__name__}"
            )

        return ADKExecutor(agent, config)

    def validate_config(self, config: AgentConfig) -> None:
        if not config.path:
            raise ConfigurationError("Agent path is required for ADK adapter")

        logger.debug("Configuration validated for ADK agent")

    def _is_adk_agent_instance(self, obj: Any) -> bool:
        if obj is None:
            return False

        # Direct type check
        if isinstance(obj, BaseAgent):
            return True

        # Class name matching for duck typing
        class_name = obj.__class__.__name__
        return class_name in self.get_supported_type_names()

"""Abstract base class for framework-specific agent adapters.

This module defines the AgentAdapter interface that all framework-specific adapters
must implement. It handles agent discovery, initialization, validation, and executor creation.

The AgentAdapter is responsible for:
- Identifying if an agent object is compatible with the framework
- Discovering agents from modules based on configuration
- Initializing agents with framework-specific setup
- Creating ExecutorAdapter instances for running agents
- Validating agent configurations before execution
- Providing framework metadata

Note: Module loading is handled by ModuleLoader in core.agent.module_loader,
not by the adapter.
"""

from abc import ABC, abstractmethod
from typing import Any

from aion.shared.config.models import AgentConfig

from .executor_adapter import ExecutorAdapter


class AgentAdapter(ABC):
    """Abstract base for framework-specific agent integration.

    Subclasses must implement all abstract methods to provide framework-specific
    agent discovery, initialization, and executor creation logic.
    """

    @staticmethod
    @abstractmethod
    def framework_name() -> str:
        """Return the name of the framework this adapter supports.

        Returns:
            str: Framework name (e.g., "langgraph", "autogen")
        """
        pass

    @abstractmethod
    def can_handle(self, agent_obj: Any) -> bool:
        """Check if the adapter can handle the given agent object.

        Args:
            agent_obj: The agent object to check compatibility with

        Returns:
            bool: True if this adapter can handle the agent, False otherwise
        """
        pass

    @abstractmethod
    def get_supported_types(self) -> list[type]:
        """Return list of types this adapter can handle.

        This is used by ModuleLoader for automatic discovery of agents
        within a module. The loader will search for instances, classes,
        or callables matching these types.

        Returns:
            list[type]: List of supported base types/classes
        """
        pass

    @abstractmethod
    def get_supported_type_names(self) -> set[str]:
        """Return set of class names this adapter can handle.

        Some types cannot be imported directly (compiled graphs, etc.),
        so we also check by class name.

        Returns:
            set[str]: Set of supported class names
        """
        pass

    @abstractmethod
    async def initialize_agent(self, agent_obj: Any, config: AgentConfig) -> Any:
        """Initialize an agent with the given configuration.

        Args:
            agent_obj: The agent object to initialize
            config: Agent configuration for initialization

        Returns:
            Any: The initialized agent, ready for execution

        Raises:
            ValueError: If initialization fails or configuration is invalid
        """
        pass

    @abstractmethod
    async def create_executor(self, agent: Any, config: AgentConfig) -> ExecutorAdapter:
        """Create an executor for the given agent.

        Args:
            agent: The initialized agent object
            config: Agent configuration

        Returns:
            ExecutorAdapter: An executor capable of running the agent
        """
        pass

    @abstractmethod
    def validate_config(self, config: AgentConfig) -> None:
        """Validate agent configuration for this framework.

        Args:
            config: Agent configuration to validate

        Raises:
            ValueError: If configuration is invalid for this framework
        """
        pass

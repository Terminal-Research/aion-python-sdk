"""Unified agent representation for AION server.

This module provides the AionAgent class, which serves as a framework-agnostic
representation of an agent in the AION system. It encapsulates agent identity,
configuration, and execution capabilities while delegating framework-specific
operations to adapters.
"""
from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any, Optional, TYPE_CHECKING

from aion.shared.agent.adapters import (
    ExecutionConfig,
    AgentAdapter,
    ExecutionSnapshot,
    ExecutionEvent,
    ExecutorAdapter,
)
from aion.shared.agent.card import AionAgentCard
from aion.shared.agent.inputs import AgentInput
from aion.shared.config.models import AgentConfig
from aion.shared.logging.base import AionLogger

from .models import AgentMetadata

if TYPE_CHECKING:
    from aion.shared.logging.base import AionLogger


def _get_logger() -> AionLogger:
    from aion.shared.logging.factory import get_logger
    return get_logger()


class AionAgent:
    """Unified agent representation for all frameworks.

    This is a domain entity that represents an agent in the AION system,
    regardless of the underlying framework (LangGraph, AutoGen, etc.).

    The AionAgent provides a framework-agnostic interface for agent operations
    while delegating framework-specific logic to adapters.

    Architecture:
        - AionAgent: Framework-agnostic domain entity (this class)
        - AgentAdapter: Framework discovery and initialization
        - ExecutorAdapter: Framework-specific execution logic
        - Native agent: Framework's native agent object (encapsulated)

    Note:
        This class is designed for single-agent deployment. For multi-agent
        scenarios, deploy multiple servers behind a proxy.
    """

    def __init__(
            self,
            agent_id: str,
            config: AgentConfig,
            adapter: Optional[AgentAdapter] = None,
            executor: Optional[ExecutorAdapter] = None,
            native_agent: Optional[Any] = None,
            port: Optional[int] = None,
            metadata: Optional[AgentMetadata] = None,
            logger: Optional[AionLogger] = None,
    ):
        """Initialize AionAgent.

        Args:
            agent_id: Unique agent identifier
            config: Agent configuration
            adapter: Framework-specific adapter (optional, set during build)
            executor: Framework-specific executor (optional, set during build)
            native_agent: Native framework agent object (optional, set during build)
            port: Port number
            metadata: Optional agent metadata
            logger: Optional AionLogger instance

        Note:
            You can create an agent with just agent_id and config, then call build()
            to complete initialization. Or use AionAgent.from_adapter() or
            AionAgent.from_config() factory methods for one-step creation.
        """
        self._id = agent_id
        self.port = port
        self._config = config
        self._adapter = adapter
        self._executor = executor
        self._native_agent = native_agent
        self._is_built = False

        # Create default metadata if not provided
        if metadata is None:
            metadata = AgentMetadata(created_at=time.time())

        self._metadata = metadata
        self._card: Optional[Any] = None
        self._logger: Optional[AionLogger] = logger

    @property
    def logger(self) -> AionLogger:
        if not self._logger:
            self._logger = _get_logger()
        return self._logger

    @property
    def id(self) -> str:
        """Agent unique identifier."""
        return self._id

    @property
    def port(self):
        """Port number where agent is running."""
        return self._port

    @port.setter
    def port(self, port: Optional[int]) -> None:
        """Set port number."""
        if port is None:
            self._port = None
            return

        if not isinstance(port, int):
            raise TypeError("Port must be an integer")

        if port <= 0 or port > 65535:
            raise ValueError("Port number must be between 0 and 65535")
        self._port = port

    @property
    def config(self) -> AgentConfig:
        """Agent configuration."""
        return self._config

    @property
    def is_built(self) -> bool:
        """Check if agent has been built (framework discovered and executor created).

        Returns:
            bool: True if agent is ready for execution, False otherwise
        """
        return self._is_built

    @property
    def framework(self) -> str:
        """Framework name (e.g., 'langgraph', 'autogen').

        Returns framework from config if available, otherwise from adapter.
        """
        if hasattr(self._config, "framework") and self._config.framework:
            return self._config.framework
        if self._adapter:
            return self._adapter.framework_name()
        return "unknown"

    @property
    def version(self) -> str:
        """Agent version from configuration."""
        return self._config.version

    @property
    def card(self) -> Any:
        """Agent card with capabilities and metadata.

        Lazy-loaded on first access.
        """
        if self._card is None:
            self._card = AionAgentCard.from_config(
                config=self._config,
                base_url=f"http://{self.host}:{self.port}")
        return self._card

    @property
    def metadata(self) -> AgentMetadata:
        """Agent runtime metadata."""
        return self._metadata

    @property
    def host(self):
        return "0.0.0.0"

    async def stream(
            self,
            inputs: AgentInput | dict[str, Any],
            context_id: Optional[str] = None,
            task_id: Optional[str] = None,
            timeout: Optional[float] = None,
            **metadata,
    ) -> AsyncIterator[ExecutionEvent]:
        """Execute agent with streaming output.

        Args:
            inputs: Input data (AgentInput or dict for backward compatibility)
            context_id: Context identifier for multi-turn conversations (A2A context_id)
            task_id: Optional task identifier for this specific execution (A2A task.id)
            timeout: Maximum execution time in seconds
            **metadata: Additional execution metadata

        Yields:
            ExecutionEvent: Events emitted during execution

        Raises:
            RuntimeError: If agent is not built yet
            ExecutionError: If execution fails
            TimeoutError: If execution exceeds timeout
        """
        if not self._is_built:
            raise RuntimeError(
                f"Agent '{self._id}' is not built yet. Call build() before executing."
            )

        config = ExecutionConfig(
            task_id=task_id,
            context_id=context_id,
            timeout=timeout,
            metadata=metadata,
        )

        self.logger.debug(
            f"Streaming agent '{self.id}' (framework={self.framework}, "
            f"task_id={task_id}, context_id={context_id})"
        )

        # Convert dict to AgentInput for backward compatibility
        if isinstance(inputs, dict):
            inputs = AgentInput.from_dict(inputs)

        async for event in self._executor.stream(inputs, config):
            yield event

    async def get_state(
            self,
            context_id: str,
            task_id: Optional[str] = None,
    ) -> ExecutionSnapshot:
        """Get current execution state snapshot for a context.

        Args:
            context_id: Context identifier (A2A context_id)
            task_id: Optional task identifier (A2A task.id)

        Returns:
            ExecutionSnapshot: Current execution snapshot including state, messages, status, and metadata

        Raises:
            RuntimeError: If agent is not built yet
            StateRetrievalError: If state cannot be retrieved
        """
        if not self._is_built:
            raise RuntimeError(
                f"Agent '{self._id}' is not built yet. Call build() before accessing state."
            )

        config = ExecutionConfig(task_id=task_id, context_id=context_id)

        self.logger.debug(
            f"Getting state for agent '{self.id}', task_id={task_id}, context_id={context_id}"
        )

        return await self._executor.get_state(config)

    async def resume(
            self,
            context_id: str,
            inputs: Optional[AgentInput | dict[str, Any]] = None,
            task_id: Optional[str] = None,
            **metadata,
    ) -> AsyncIterator[ExecutionEvent]:
        """Resume interrupted execution.

        Args:
            context_id: Context identifier (A2A context_id)
            inputs: Optional input data (AgentInput or dict for backward compatibility)
            task_id: Optional task identifier for this specific execution (A2A task.id)
            **metadata: Additional execution metadata

        Yields:
            ExecutionEvent: Events emitted during resumed execution

        Raises:
            RuntimeError: If agent is not built yet
            ExecutionError: If resume fails
            ValueError: If execution is not in resumable state
        """
        if not self._is_built:
            raise RuntimeError(
                f"Agent '{self._id}' is not built yet. Call build() before resuming."
            )

        config = ExecutionConfig(
            task_id=task_id,
            context_id=context_id,
            metadata=metadata,
        )

        self.logger.debug(
            f"Resuming agent '{self.id}', task_id={task_id}, context_id={context_id}"
        )

        # Convert dict to AgentInput for backward compatibility
        if inputs is not None and isinstance(inputs, dict):
            inputs = AgentInput.from_dict(inputs)

        async for event in self._executor.resume(inputs, config):
            yield event

    # ===== Factory Methods =====

    @classmethod
    async def from_adapter(
            cls,
            agent_id: str,
            config: AgentConfig,
            adapter: AgentAdapter,
            native_agent: Any,
    ) -> "AionAgent":
        """Create AionAgent from adapter and native agent.

        This is the primary factory method for creating AionAgent instances.

        Args:
            agent_id: Unique agent identifier
            config: Agent configuration
            adapter: Framework-specific adapter
            native_agent: Native framework agent object (Graph, AssistantAgent, etc.)

        Returns:
            AionAgent: Unified agent instance

        Raises:
            ValueError: If configuration is invalid
            TypeError: If native_agent cannot be initialized
        """
        logger = _get_logger()
        logger.debug(
            f"Creating AionAgent '{agent_id}' from adapter "
            f"(framework={adapter.framework_name()})"
        )

        # Validate config
        adapter.validate_config(config)

        # Initialize agent (may wrap or transform native_agent)
        initialized_agent = await adapter.initialize_agent(native_agent, config)

        # Create executor
        executor = await adapter.create_executor(initialized_agent, config)

        # Create metadata with runtime capabilities
        metadata = AgentMetadata(created_at=time.time())

        agent = cls(
            agent_id=agent_id,
            config=config,
            adapter=adapter,
            executor=executor,
            native_agent=initialized_agent,
            metadata=metadata,
        )

        logger.info(
            f"AionAgent '{agent_id}' created successfully "
            f"(framework={adapter.framework_name()})"
        )

        return agent

    async def build(self, base_path: Optional[Any] = None) -> "AionAgent":
        """Build the agent by discovering framework and creating executor.

        This method completes the agent initialization by:
        1. Loading the agent module from config.path
        2. Auto-detecting the framework using registered adapters
        3. Creating the executor for agent execution

        This allows plugins to be registered before the framework is discovered.

        Args:
            base_path: Optional base path for resolving relative module paths

        Returns:
            AionAgent: Self for method chaining

        Raises:
            RuntimeError: If agent is already built
            ValueError: If no adapter can handle the agent or path is missing
            FileNotFoundError: If agent module not found
        """
        if self._is_built:
            raise RuntimeError(f"Agent '{self._id}' is already built")

        from ..adapters.registry import adapter_registry
        from .module_loader import ModuleLoader

        if not self._config.path:
            raise ValueError(f"Agent path is required in configuration for agent '{self._id}'")

        self.logger.debug(f"Building AionAgent '{self._id}' from config (path='{self._config.path}')")

        # Load the module using ModuleLoader
        module_loader = ModuleLoader(base_path=base_path)
        try:
            module, item_name = module_loader.load_from_config_path(self._config.path)
        except Exception as ex:
            raise FileNotFoundError(
                f"Failed to load module for agent '{self._id}' from path '{self._config.path}': {ex}"
            ) from ex

        # Try each registered adapter to discover the agent
        errors = []
        for adapter in adapter_registry.list_adapters():
            try:
                # Try to discover object in module using adapter's supported types
                native_agent = module_loader.discover_object(
                    module=module,
                    supported_types=adapter.get_supported_types(),
                    item_name=item_name
                )

                # Check if adapter can handle the discovered agent
                if adapter.can_handle(native_agent):
                    self.logger.debug(
                        f"Auto-detected framework '{adapter.framework_name()}' for agent '{self._id}'"
                    )

                    # Validate config
                    adapter.validate_config(self._config)

                    # Initialize agent (may wrap or transform native_agent)
                    initialized_agent = await adapter.initialize_agent(native_agent, self._config)

                    # Create executor
                    executor = await adapter.create_executor(initialized_agent, self._config)

                    # Set the components
                    self._adapter = adapter
                    self._executor = executor
                    self._native_agent = initialized_agent
                    self._is_built = True

                    self.logger.info(
                        f"AionAgent '{self._id}' built successfully "
                        f"(framework={adapter.framework_name()})"
                    )

                    return self

            except Exception as ex:
                # Store error and try next adapter
                errors.append(f"{adapter.framework_name()}: {ex}")
                continue

        # No adapter could handle the agent
        available_frameworks = [_adap.framework_name() for _adap in adapter_registry.list_adapters()]
        error_msg = f"No adapter found for agent '{self._id}' in module '{self._config.path}'.\n"
        error_msg += f"Available frameworks: {available_frameworks}\n"
        error_msg += f"Errors encountered:\n" + "\n".join(f"  - {err}" for err in errors)
        raise ValueError(error_msg)

    def get_native_agent(self) -> Any:
        """Get the native framework agent object.

        Warning:
            This breaks framework abstraction. Use only when framework-specific
            operations are required. Prefer the unified API methods.

        Returns:
            Any: Native agent object
        """
        return self._native_agent

    def get_adapter(self) -> AgentAdapter:
        """Get the framework adapter.

        Returns:
            AgentAdapter: Framework-specific adapter
        """
        return self._adapter

    def get_executor(self) -> ExecutorAdapter:
        """Get the executor adapter.

        Returns:
            ExecutorAdapter: Framework-specific executor
        """
        return self._executor

    def __repr__(self) -> str:
        """Return string representation of the agent."""
        return (
            f"AionAgent(id={self.id!r}, framework={self.framework!r}, "
            f"version={self.version!r})"
        )

    def __str__(self) -> str:
        """Return human-readable string representation."""
        return f"AionAgent '{self.id}' ({self.framework})"

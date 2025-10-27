"""Unified agent representation for AION server.

This module provides the AionAgent class, which serves as a framework-agnostic
representation of an agent in the AION system. It encapsulates agent identity,
configuration, and execution capabilities while delegating framework-specific
operations to adapters.
"""
import time
from collections.abc import AsyncIterator
from typing import Any, Optional

from aion.shared.agent.adapters import (
    ExecutionConfig,
    AgentAdapter,
    AgentState,
    ExecutionEvent,
    ExecutorAdapter,
)
from aion.shared.agent.card import AionAgentCard
from aion.shared.config.models import AgentConfig
from aion.shared.logging.factory import get_logger
from .models import AgentMetadata

logger = get_logger()


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
            adapter: AgentAdapter,
            executor: ExecutorAdapter,
            native_agent: Any,
            metadata: Optional[AgentMetadata] = None,
    ):
        """Initialize AionAgent.

        Args:
            agent_id: Unique agent identifier
            config: Agent configuration
            adapter: Framework-specific adapter
            executor: Framework-specific executor
            native_agent: Native framework agent object
            metadata: Optional agent metadata

        Note:
            Don't call this directly. Use AionAgent.from_adapter() or
            AionAgent.from_config() factory methods instead.
        """
        self._id = agent_id
        self._config = config
        self._adapter = adapter
        self._executor = executor
        self._native_agent = native_agent

        # Create default metadata if not provided
        if metadata is None:
            metadata = AgentMetadata(created_at=time.time())

        self._metadata = metadata
        self._card: Optional[Any] = None

    @property
    def logger(self):
        if not hasattr(self, "_logger"):
            self._logger = get_logger()
        return self._logger

    @property
    def id(self) -> str:
        """Agent unique identifier."""
        return self._id

    @property
    def config(self) -> AgentConfig:
        """Agent configuration."""
        return self._config

    @property
    def framework(self) -> str:
        """Framework name (e.g., 'langgraph', 'autogen').

        Returns framework from config if available, otherwise from adapter.
        """
        return self._config.framework if hasattr(self._config, "framework") else self._adapter.framework_name()

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
            self._card = AionAgentCard.from_config(self._config)
        return self._card

    @property
    def metadata(self) -> AgentMetadata:
        """Agent runtime metadata."""
        return self._metadata

    @property
    def host(self):
        return "0.0.0.0"

    @property
    def port(self) -> int:
        return self.config.port

    async def execute(
            self,
            inputs: dict[str, Any],
            session_id: Optional[str] = None,
            thread_id: Optional[str] = None,
            timeout: Optional[float] = None,
            **metadata,
    ) -> dict[str, Any]:
        """Execute agent with given inputs (non-streaming).

        This is a framework-agnostic method that works for all agents.

        Args:
            inputs: Input data for the agent
            session_id: Session identifier for this execution
            thread_id: Thread identifier for multi-turn conversations
            timeout: Maximum execution time in seconds
            **metadata: Additional execution metadata

        Returns:
            dict[str, Any]: Agent execution result

        Raises:
            ExecutionError: If execution fails
            TimeoutError: If execution exceeds timeout
        """
        config = ExecutionConfig(
            session_id=session_id,
            thread_id=thread_id,
            timeout=timeout,
            metadata=metadata,
        )

        logger.debug(
            f"Executing agent '{self.id}' (framework={self.framework}, "
            f"session_id={session_id})"
        )

        return await self._executor.invoke(inputs, config)

    async def stream(
            self,
            inputs: dict[str, Any],
            session_id: Optional[str] = None,
            thread_id: Optional[str] = None,
            timeout: Optional[float] = None,
            **metadata,
    ) -> AsyncIterator[ExecutionEvent]:
        """Execute agent with streaming output.

        Args:
            inputs: Input data for the agent
            session_id: Session identifier for this execution
            thread_id: Thread identifier for multi-turn conversations
            timeout: Maximum execution time in seconds
            **metadata: Additional execution metadata

        Yields:
            ExecutionEvent: Events emitted during execution

        Raises:
            ExecutionError: If execution fails
            TimeoutError: If execution exceeds timeout
        """
        config = ExecutionConfig(
            session_id=session_id,
            thread_id=thread_id,
            timeout=timeout,
            metadata=metadata,
        )

        logger.debug(
            f"Streaming agent '{self.id}' (framework={self.framework}, "
            f"session_id={session_id})"
        )

        async for event in self._executor.stream(inputs, config):
            yield event

    async def get_state(
            self,
            session_id: str,
            thread_id: Optional[str] = None,
    ) -> AgentState:
        """Get current execution state for a session.

        Args:
            session_id: Session identifier
            thread_id: Optional thread identifier

        Returns:
            AgentState: Current agent state

        Raises:
            StateRetrievalError: If state cannot be retrieved
        """
        config = ExecutionConfig(session_id=session_id, thread_id=thread_id)

        logger.debug(f"Getting state for agent '{self.id}', session={session_id}")

        return await self._executor.get_state(config)

    async def resume(
            self,
            session_id: str,
            inputs: Optional[dict[str, Any]] = None,
            thread_id: Optional[str] = None,
            **metadata,
    ) -> AsyncIterator[ExecutionEvent]:
        """Resume interrupted execution.

        Args:
            session_id: Session identifier
            inputs: Optional input data to provide after interruption
            thread_id: Optional thread identifier
            **metadata: Additional execution metadata

        Yields:
            ExecutionEvent: Events emitted during resumed execution

        Raises:
            ExecutionError: If resume fails
            ValueError: If execution is not in resumable state
        """
        config = ExecutionConfig(
            session_id=session_id,
            thread_id=thread_id,
            metadata=metadata,
        )

        logger.debug(f"Resuming agent '{self.id}', session={session_id}")

        async for event in self._executor.resume(inputs, config):
            yield event

    # ===== Capability Checks =====

    def supports_streaming(self) -> bool:
        """Check if agent supports streaming execution.

        Returns:
            bool: True if streaming is supported
        """
        return self._executor.supports_streaming()

    def supports_resume(self) -> bool:
        """Check if agent supports resuming from interrupts.

        Returns:
            bool: True if resume is supported
        """
        return self._executor.supports_resume()

    def supports_state_retrieval(self) -> bool:
        """Check if agent supports state retrieval.

        Returns:
            bool: True if state retrieval is supported
        """
        return self._executor.supports_state_retrieval()

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

    @classmethod
    async def from_config(
            cls,
            agent_id: str,
            config: AgentConfig,
            base_path: Optional[Any] = None,
    ) -> "AionAgent":
        """Create AionAgent from config by auto-detecting framework.

        Uses ModuleLoader to load the module, then tries all registered adapters
        to find one that can discover and handle the agent.

        Args:
            agent_id: Unique agent identifier
            config: Agent configuration with agent path
            base_path: Optional base path for resolving relative module paths

        Returns:
            AionAgent: Unified agent instance

        Raises:
            ValueError: If no adapter can handle the agent or path is missing
            FileNotFoundError: If agent module not found
        """
        from ..adapters.registry import adapter_registry
        from .module_loader import ModuleLoader

        if not config.path:
            raise ValueError(f"Agent path is required in configuration for agent '{agent_id}'")

        logger.debug(f"Creating AionAgent '{agent_id}' from config (path='{config.path}')")

        # Load the module using ModuleLoader
        module_loader = ModuleLoader(base_path=base_path)
        try:
            module, item_name = module_loader.load_from_config_path(config.path)
            logger.debug(f"Successfully loaded module for agent '{agent_id}'")
        except Exception as ex:
            raise FileNotFoundError(
                f"Failed to load module for agent '{agent_id}' from path '{config.path}': {ex}"
            ) from ex

        # Try each registered adapter to discover the agent
        errors = []
        for adapter in adapter_registry.list_adapters():
            try:
                # Get supported types from adapter
                supported_types = adapter.get_supported_types()
                supported_type_names = adapter.get_supported_type_names()

                # Try to discover object in module using adapter's supported types
                native_agent = module_loader.discover_object(
                    module=module,
                    supported_types=supported_types,
                    supported_type_names=supported_type_names,
                    item_name=item_name
                )

                # Check if adapter can handle the discovered agent
                if adapter.can_handle(native_agent):
                    logger.debug(
                        f"Auto-detected framework '{adapter.framework_name()}' for agent '{agent_id}'"
                    )
                    return await cls.from_adapter(agent_id, config, adapter, native_agent)

            except Exception as ex:
                # Store error and try next adapter
                errors.append(f"{adapter.framework_name()}: {ex}")
                continue

        # No adapter could handle the agent
        available_frameworks = [_adap.framework_name() for _adap in adapter_registry.list_adapters()]
        error_msg = f"No adapter found for agent '{agent_id}' in module '{config.path}'.\n"
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

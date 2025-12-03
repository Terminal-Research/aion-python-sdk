"""Agent plugin protocol for framework integrations.

This module defines the protocol specifically for agent framework plugins,
extending the base plugin protocol with agent-specific capabilities.
"""

from abc import abstractmethod

from .base import BasePluginProtocol


class AgentPluginProtocol(BasePluginProtocol):
    """Protocol for agent framework plugins.

    Agent plugins integrate external agent frameworks (LangGraph, CrewAI, AutoGen)
    into the AION system. They handle:
    - Framework-specific setup and lifecycle
    - Database migrations (e.g., checkpointer tables)
    - Providing runtime adapters for agent execution

    The plugin manages infrastructure, while the adapter handles runtime execution.
    """

    @abstractmethod
    def get_adapter(self):
        """Get the runtime adapter for this agent framework.

        The adapter handles agent initialization, execution, streaming,
        state management, and checkpointing.

        Returns:
            AgentAdapter: The adapter instance for agent operations

        Raises:
            RuntimeError: If called before setup()
        """
        pass

"""Agent plugin protocol for framework integrations.

This module defines the protocol specifically for agent framework plugins,
extending the base plugin protocol with agent-specific capabilities.
"""

from abc import abstractmethod

from fastapi import FastAPI

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

    async def configure_app(self, app: FastAPI, agent) -> None:
        """Configure the FastAPI application with plugin customizations.

        Optional hook called AFTER both the FastAPI app and agent are built.
        This is Phase 2 of the plugin lifecycle, allowing plugins to integrate
        with the running application.

        Use this method to:
        - Add custom routes to the FastAPI app
        - Add middlewares
        - Access the built agent (including native_agent)
        - Customize OpenAPI schema
        - Add framework-specific endpoints
        """
        pass

"""Agent factory for managing agent initialization.

This module provides AgentFactory which handles agent building,
framework discovery, and executor creation.
"""

from typing import Optional, Any

from aion.shared.agent import AionAgent
from aion.shared.logging import get_logger

logger = get_logger()


class AgentFactory:
    """Factory for agent initialization and building.

    Handles:
    - Agent building (framework discovery)
    - Executor creation
    - Error handling during initialization
    """

    def __init__(self, aion_agent: AionAgent):
        """Initialize the agent factory.

        Args:
            aion_agent: AionAgent instance to build
        """
        self.aion_agent = aion_agent

    async def build(self, base_path: Optional[Any] = None) -> AionAgent:
        """Build the agent by discovering framework and creating executor.

        This method must be called after plugins are initialized so that
        framework adapters are registered and available for discovery.

        Args:
            base_path: Optional base path for resolving module imports

        Returns:
            AionAgent: The built agent instance

        Raises:
            RuntimeError: If agent is already built
            ValueError: If no adapter can handle the agent or path is missing
            FileNotFoundError: If agent module not found
        """
        if self.aion_agent.is_built:
            logger.debug(f"Agent '{self.aion_agent.id}' is already built, skipping")
            return self.aion_agent

        try:
            logger.debug(f"Building agent '{self.aion_agent.id}'")
            await self.aion_agent.build(base_path=base_path)
            logger.info(f"Agent '{self.aion_agent.id}' built successfully")
            return self.aion_agent
        except FileNotFoundError as exc:
            logger.error(
                f"Agent module not found for '{self.aion_agent.id}': {exc}",
                exc_info=exc
            )
            raise
        except ValueError as exc:
            logger.error(
                f"Failed to build agent '{self.aion_agent.id}': {exc}",
                exc_info=exc
            )
            raise
        except Exception as exc:
            logger.error(
                f"Unexpected error building agent '{self.aion_agent.id}': {exc}",
                exc_info=exc
            )
            raise

    @property
    def is_built(self) -> bool:
        """Check if agent is built.

        Returns:
            bool: True if agent is built, False otherwise
        """
        return self.aion_agent.is_built


__all__ = ["AgentFactory"]

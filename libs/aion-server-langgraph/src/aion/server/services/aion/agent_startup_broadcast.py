from aion.api.gql import AionGqlContextClient
from aion.shared.aion_config import AgentConfig

from aion.server.core.base import BaseExecuteService


class AionAgentStartupBroadcastService(BaseExecuteService):
    """
    Service for broadcasting agent startup information to the Aion platform.

    This service registers the agent version and configuration with the Aion platform
    during application startup, enabling proper agent tracking and version management.

    The service uses the GraphQL client to communicate with the Aion platform and
    handles initialization failures gracefully by logging warnings instead of raising
    exceptions.

    Example:
        service = AionAgentStartupBroadcastService()
        await service.execute(agent_config)
    """

    async def execute(self, agent_config: AgentConfig) -> bool:
        """
        Execute the agent startup broadcast to the Aion platform.

        Establishes a connection to the Aion platform via GraphQL client and
        registers the agent's version and configuration information. If the
        client initialization fails, logs a warning.

        Args:
            agent_config: Configuration object containing agent metadata
                         such as version, capabilities, and identification info.

        Returns:
            None

        Note:
            This method is designed to be non-blocking for application startup.
            Connection failures are logged as warnings rather than exceptions
            to ensure the agent can start even if the platform is temporarily
            unavailable.
        """
        try:
            async with AionGqlContextClient() as client:
                await client.register_version(agent_config)
                self.logger.info("Agent startup broadcast completed successfully")
            return True
        except Exception as ex:
            self.logger.error("Agent startup broadcast failed: %s", ex)
            return False

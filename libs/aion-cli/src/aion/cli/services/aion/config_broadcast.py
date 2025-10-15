from aion.api.gql import AionGqlContextClient
from aion.shared.aion_config import AionConfig

from aion.shared.base import BaseExecuteService


class AionConfigBroadcastService(BaseExecuteService):
    """
    Service for broadcasting complete configuration to the Aion platform.

    This service registers the full Aion configuration including all agents and proxy
    settings with the Aion platform during application startup, enabling proper
    configuration tracking and version management.

    The service uses the GraphQL client to communicate with the Aion platform and
    handles initialization failures gracefully by logging errors instead of raising
    exceptions.

    Example:
        service = AionConfigBroadcastService()
        await service.execute(config)
    """

    async def execute(self, config: AionConfig) -> bool:
        """
        Execute the configuration broadcast to the Aion platform.

        Establishes a connection to the Aion platform via GraphQL client and
        registers the complete configuration including all agents and proxy settings.
        If the client initialization fails, logs an error.

        Args:
            config: The complete Aion configuration to broadcast

        Returns:
            bool: True if broadcast was successful, False otherwise

        Note:
            This method is designed to be non-blocking for application startup.
            Connection failures are logged as errors rather than exceptions
            to ensure the application can start even if the platform is temporarily
            unavailable.
        """
        try:
            async with AionGqlContextClient() as client:
                await client.register_version(config)
                self.logger.info("Configuration broadcast completed successfully")
            return True
        except Exception as ex:
            self.logger.error("Configuration broadcast failed: %s", ex)
            return False

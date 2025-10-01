from typing import Optional

from aion.api.gql import AionGqlClient
from aion.shared.settings import api_settings
from aion.api.http import AionJWTManager, aion_jwt_manager


class AionGqlContextClient:
    """
    A manager class for handling AionGqlClient instances with context management capabilities.
    """

    def __init__(
            self,
            client_id: Optional[str] = None,
            client_secret: Optional[str] = None,
            jwt_manager: Optional[AionJWTManager] = None,
            gql_url: Optional[str] = None,
            ws_url: Optional[str] = None
    ):
        """
        Initialize the AionGqlContextClient.

        Args:
            client_id: Client ID for authentication
            client_secret: Client secret for authentication
            jwt_manager: JWT manager instance
            gql_url: GraphQL endpoint URL
            ws_url: WebSocket URL for GraphQL subscriptions
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.jwt_manager = jwt_manager
        self.gql_url = gql_url
        self.ws_url = ws_url
        self._client: Optional[AionGqlClient] = None

    def _create_client(self) -> AionGqlClient:
        """
        Create and return a new AionGqlClient instance with configured parameters.

        Returns:
            Configured AionGqlClient instance
        """
        return AionGqlClient(
            client_id=self.client_id or api_settings.client_id,
            client_secret=self.client_secret or api_settings.client_secret,
            jwt_manager=self.jwt_manager or aion_jwt_manager,
            gql_url=self.gql_url or api_settings.gql_url,
            ws_url=self.ws_url or api_settings.ws_gql_url
        )

    async def __aenter__(self) -> AionGqlClient:
        """
        Async context manager entry point.

        Returns:
            Initialized AionGqlClient instance
        """
        self._client = self._create_client()
        await self._client.initialize()
        return self._client

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """
        Async context manager exit point.

        Args:
            exc_type: Exception type
            exc_val: Exception value
            exc_tb: Exception traceback
        """
        if self._client:
            # Add cleanup logic here if needed
            # await self._client.close()
            self._client = None

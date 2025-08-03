import logging
from typing import Optional, List, Any, AsyncIterator

from aion.api.config import aion_api_settings
from aion.api.http import aion_jwt_manager
from .generated.graphql_client import (
    MessageInput,
    ChatCompletionStream,
    JSONRPCRequestInput,
    A2AStream,
)
from .generated.graphql_client.client import GqlClient

logger = logging.getLogger(__name__)


class AionGqlClient:
    """
    Lightweight wrapper over ariadne-codegen generated GraphQL client with automatic authentication.

    This wrapper provides seamless integration with the Aion GraphQL API by handling
    JWT token authentication automatically. It wraps the generated GqlClient from
    ariadne-codegen and manages the authentication flow transparently.

    Attributes:
        client_id (str): Client identifier for authentication
        secret (str): Client secret for authentication
        client (Optional[GqlClient]): The underlying ariadne-codegen generated GraphQL client
    """

    def __init__(self, client_id: Optional[str] = None, client_secret: Optional[str] = None):
        """
        Initialize the Aion GraphQL client.

        Args:
            client_id (Optional[str]): Client ID for authentication.
                If not provided, uses value from aion_api_settings.
            client_secret (Optional[str]): Client secret for authentication.
                If not provided, uses value from aion_api_settings.
        """
        self.client_id = client_id or aion_api_settings.client_id
        self.secret = client_secret or aion_api_settings.client_secret
        self.client: Optional[GqlClient] = None
        self._is_initialized = False

    async def initialize(self):
        """
        Initialize the GraphQL client with authentication.

        This method sets up the client with proper JWT authentication
        and establishes connections for both HTTP and WebSocket endpoints.
        """
        if self._is_initialized:
            logger.warning("AionGqlClient is already initialized")
            return

        logger.info(f"Initializing AionGqlClient...")
        await self._build_client()
        self._is_initialized = True

    def _validate_client_before_execute(self):
        """
        Validate that the client is properly initialized before executing operations.

        Raises:
            RuntimeError: If the client has not been initialized via initialize() method.
        """
        if not self._is_initialized:
            error_msg = "AionGqlClient is not initialized before executing operations."
            logger.error(error_msg)
            raise RuntimeError(error_msg)

    async def _build_client(self):
        """
        Build and configure the underlying GraphQL client.

        Creates a new GqlClient instance with authenticated URLs for both
        HTTP and WebSocket connections. Handles JWT token retrieval and
        URL construction with proper authentication parameters.
        """
        if isinstance(self.client, GqlClient):
            logger.warning("Client already initialized")
            return

        aion_token = await aion_jwt_manager.get_token()
        if not aion_token:
            raise ValueError("No token received from authentication")

        http_url = (
            f"{aion_api_settings.gql_url}"
            f"?token={aion_token}"
        )
        ws_url = (
            f"{aion_api_settings.ws_gql_url}"
            f"?token={aion_token}"
        )

        self.client = GqlClient(
            url=http_url,
            ws_url=ws_url)

    async def chat_completion_stream(
            self,
            model: str,
            messages: List[MessageInput],
            stream: bool,
            **kwargs: Any
    ) -> AsyncIterator[ChatCompletionStream]:
        """Stream chat completion responses from the Aion API.

        Provides a streaming interface for chat completions, allowing real-time
        processing of AI responses.

        Args:
            model (str): The AI model to use for completion
            messages (List[MessageInput]): List of input messages for the conversation
            stream (bool): Whether to enable streaming mode
            **kwargs (Any): Additional parameters to pass to the completion request
        """
        self._validate_client_before_execute()

        async for chunk in self.client.chat_completion_stream(
            model=model,
            messages=messages,
            stream=stream,
            **kwargs):
            yield chunk

    async def a2a_stream(
            self,
            request: JSONRPCRequestInput,
            distribution_id: str,
            **kwargs: Any
    ) -> AsyncIterator[A2AStream]:
        """Stream agent-to-agent JSON-RPC responses.

        Opens a websocket subscription to the A2A pipeline, yielding
        incremental JSON-RPC responses produced during agent workflow
        execution.

        Args:
            request (JSONRPCRequestInput): JSON-RPC request payload.
            distribution_id (str): Identifier of the distribution to handle the request.
            **kwargs (Any): Additional parameters forwarded to the underlying client.
        """
        self._validate_client_before_execute()

        async for chunk in self.client.a_2_a_stream(
            request=request,
            distribution_id=distribution_id,
            **kwargs):
            yield chunk

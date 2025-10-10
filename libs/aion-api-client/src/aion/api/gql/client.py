import logging
from typing import Optional, List, Any, AsyncIterator

from aion.shared.aion_config import AionConfig

from aion.api.http import AionJWTManager
from .generated.graphql_client import (
    MessageInput,
    ChatCompletionStream,
    JSONRPCRequestInput,
    A2AStream,
)
from .generated.graphql_client.client import GqlClient
from .generated.graphql_client.custom_mutations import Mutation

logger = logging.getLogger(__name__)


class AionGqlClient:
    """
    Lightweight wrapper over ariadne-codegen generated GraphQL client with authentication.

    This wrapper provides seamless integration with the Aion GraphQL API by handling
    JWT token authentication. It wraps the generated GqlClient from ariadne-codegen
    and manages the authentication flow. All authentication parameters (client_id,
    client_secret, and jwt_manager) must be explicitly provided during initialization.

    Attributes:
        client_id (str): Client identifier for authentication
        secret (str): Client secret for authentication
        jwt_manager (AionJWTManager): Manager used to retrieve JWT tokens
        client (Optional[GqlClient]): The underlying ariadne-codegen generated GraphQL client
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
        Initialize the Aion GraphQL client.

        Args:
            client_id (Optional[str]): Client ID for authentication.
            client_secret (Optional[str]): Client secret for authentication.
            jwt_manager (Optional[AionJWTManager]): JWT manager for token handling.
            gql_url (Optional[str]): HTTP URL for requests.
            ws_url (Optional[str]): WS URL for requests.
        """
        self.client_id = client_id
        self.secret = client_secret
        self.client: Optional[GqlClient] = None
        self.jwt_manager: AionJWTManager = jwt_manager
        self.gql_url = gql_url
        self.ws_url = ws_url

        self._is_initialized = False

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    async def initialize(self) -> "AionGqlClient":
        """
        Initialize the GraphQL client with authentication.

        This method sets up the client with proper JWT authentication
        and establishes connections for both HTTP and WebSocket endpoints.

        Raises:
            ValueError: If any required authentication parameter is not provided.
        """
        if self.is_initialized:
            logger.warning("AionGqlClient is already initialized")
            return self

        # Validate required parameters
        if not self.client_id:
            raise ValueError("client_id is required and cannot be None or empty")
        if not self.secret:
            raise ValueError("client_secret is required and cannot be None or empty")
        if self.jwt_manager is None:
            raise ValueError("jwt_manager is required and cannot be None")
        if not self.gql_url:
            raise ValueError("gql_url is required and cannot be None or empty")
        if not self.ws_url:
            raise ValueError("ws_url is required and cannot be None or empty")

        await self._build_client()
        self._is_initialized = True
        return self

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

        aion_token = await self.jwt_manager.get_token()
        if not aion_token:
            raise ValueError("No token received from authentication")

        self.client = GqlClient(
            url="{gql_url}?token={token}".format(gql_url=self.gql_url, token=aion_token),
            ws_url="{ws_url}?token={token}".format(ws_url=self.ws_url, token=aion_token))

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

    async def register_version(self, config: AionConfig):
        """Register a new agent version with the provided configuration.

        Args:
            config (AionConfig): The aion configuration object containing
        """
        self._validate_client_before_execute()

        configuration = agent_config.model_dump_json()
        register_field = Mutation.register_version(configuration)
        return await self.client.mutation(
            register_field,
            operation_name="RegisterVersion"
        )

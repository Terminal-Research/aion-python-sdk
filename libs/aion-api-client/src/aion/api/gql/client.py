from datetime import datetime, timezone
from typing import Optional, List, Any, AsyncIterator

from aion.core.logging import get_logger

from aion.api.http import AionJWTManager
from .generated.graphql_client import (
    MessageInput,
    ChatCompletionStream,
    ChatCompletionRequestInput,
    A2AJsonRpcRequestGQLInput,
    CapabilitySubjectGQLInput,
    PrincipalSelectorGQLInput,
    A2AStream,
    VersionLogs,
)
from .generated.graphql_client.client import GqlClient
from .generated.graphql_client.custom_mutations import Mutation

logger = get_logger()


def _serialize_offset_datetime(value: datetime | str) -> str:
    """Serialize a datetime value for GraphQL OffsetDateTime inputs.

    Args:
        value (datetime | str): A datetime object or preformatted RFC 3339 string.

    Returns:
        str: RFC 3339-compatible timestamp string.
    """
    if isinstance(value, str):
        return value

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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
        ws_url: Optional[str] = None,
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

    async def _execute_query(self, field: Any, operation_name: str) -> dict[str, Any]:
        """
        Execute a GraphQL query with automatic validation.

        Args:
            field: GraphQL field to query
            operation_name (str): Name of the operation for logging/debugging

        Returns:
            dict[str, Any]: Query result

        Raises:
            RuntimeError: If the client is not initialized
        """
        self._validate_client_before_execute()
        return await self.client.query(field, operation_name=operation_name)

    async def _execute_mutation(
        self, field: Any, operation_name: str
    ) -> dict[str, Any]:
        """
        Execute a GraphQL mutation with automatic validation.

        Args:
            field: GraphQL field to mutate
            operation_name (str): Name of the operation for logging/debugging

        Returns:
            dict[str, Any]: Mutation result

        Raises:
            RuntimeError: If the client is not initialized
        """
        self._validate_client_before_execute()
        return await self.client.mutation(field, operation_name=operation_name)

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
            url="{gql_url}?token={token}".format(
                gql_url=self.gql_url, token=aion_token
            ),
            ws_url="{ws_url}?token={token}".format(
                ws_url=self.ws_url, token=aion_token
            ),
        )

    async def chat_completion_stream(
        self,
        model: str,
        messages: List[MessageInput],
        stream: bool,
        principal: Optional[PrincipalSelectorGQLInput] = None,
        **kwargs: Any
    ) -> AsyncIterator[ChatCompletionStream]:
        """Stream chat completion responses from the Aion API.

        Provides a streaming interface for chat completions, allowing real-time
        processing of AI responses.

        Args:
            model (str): The AI model to use for completion
            messages (List[MessageInput]): List of input messages for the conversation
            stream (bool): Whether to enable streaming mode
            principal (Optional[PrincipalSelectorGQLInput]): Optional principal selector.
            **kwargs (Any): Additional parameters to pass to the completion request
        """
        self._validate_client_before_execute()

        async for chunk in self.client.chat_completion_stream(
            request=ChatCompletionRequestInput(
                model=model, messages=messages, stream=stream
            ),
            principal=principal,
            **kwargs
        ):
            yield chunk

    async def a2a_stream(
        self,
        request: A2AJsonRpcRequestGQLInput,
        distribution_id: str,
        principal: Optional[PrincipalSelectorGQLInput] = None,
        **kwargs: Any
    ) -> AsyncIterator[A2AStream]:
        """Stream agent-to-agent JSON-RPC responses.

        Opens a websocket subscription to the A2A pipeline, yielding
        incremental JSON-RPC responses produced during agent workflow
        execution.

        Args:
            request (A2AJsonRpcRequestGQLInput): JSON-RPC request payload.
            distribution_id (str): Identifier of the distribution to handle the request.
            principal (Optional[PrincipalSelectorGQLInput]): Optional principal selector.
            **kwargs (Any): Additional parameters forwarded to the underlying client.
        """
        self._validate_client_before_execute()

        async for chunk in self.client.a_2_a_stream(
            request=request,
            target=CapabilitySubjectGQLInput(distribution_id=distribution_id),
            principal=principal,
            **kwargs
        ):
            yield chunk

    async def version_logs(
        self,
        start_time: datetime | str,
        **kwargs: Any
    ) -> AsyncIterator[VersionLogs]:
        """Stream log events for the authenticated deployment version.

        The backend derives the version from the authenticated client
        credentials/JWT, so callers must not provide a version or organization
        identifier.

        Args:
            start_time (datetime | str): Inclusive lower bound for log events.
                Datetimes are serialized as UTC RFC 3339 strings. Strings are
                forwarded unchanged.
            **kwargs (Any): Additional parameters forwarded to the generated
                websocket subscription client.

        Yields:
            VersionLogs: Generated GraphQL subscription payload.
        """
        self._validate_client_before_execute()

        async for chunk in self.client.version_logs(
            start_time=_serialize_offset_datetime(start_time),
            **kwargs
        ):
            yield chunk

    async def register_version(self, version_id: Optional[str] = None):
        """Register deployment version metadata with the Aion API.

        Starts backend-side registration for the deployment version. Runtime
        metadata is resolved by the backend during registration, so this method
        no longer serializes or sends a deployment manifest.

        Args:
            version_id (Optional[str]): Version ID to register. User-authenticated
                callers should provide this value. Version-authenticated callers
                can omit it so the backend uses the authenticated version.
        """
        from .generated.graphql_client.custom_fields import AgentBehaviorFields

        register_field = Mutation.register_version(version_id=version_id).fields(
            AgentBehaviorFields.id,
            AgentBehaviorFields.organization_id,
            AgentBehaviorFields.deployment_id,
            AgentBehaviorFields.version_id,
            AgentBehaviorFields.behavior_key,
            AgentBehaviorFields.name,
            AgentBehaviorFields.description,
            AgentBehaviorFields.logical_version,
            AgentBehaviorFields.kind,
            AgentBehaviorFields.configuration_schema,
            AgentBehaviorFields.agent_card,
        )
        return await self._execute_mutation(
            register_field, operation_name="RegisterVersion"
        )

    async def get_current_deployment_version(self) -> Optional[str]:
        """
        Fetch the current deployment version ID from control plane.

        Returns:
            Optional[str]: VERSION_ID if found, None otherwise
        """
        from .generated.graphql_client.custom_queries import Query

        version_id_field = Query.version_id_by_client_id(self.client_id)
        result = await self._execute_query(
            version_id_field, operation_name="GetVersionIdByClientId"
        )
        return result.get("versionIdByClientId")

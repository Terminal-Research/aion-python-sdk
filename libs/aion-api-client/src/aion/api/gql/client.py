"""Low level GraphQL client for communicating with the Aion API."""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from gql import Client, gql
from gql.transport.websockets import WebsocketsTransport

from aion.api.config import aion_api_settings
from aion.api.http import aion_jwt_manager

logger = logging.getLogger(__name__)


class GqlClient:
    """
    Client for the Aion GraphQL API using WebSocket connections.

    This client provides asynchronous GraphQL query execution and subscription
    handling over WebSocket connections. It automatically manages JWT authentication
    and connection lifecycle.
    """

    def __init__(self) -> None:
        self.client_id = os.getenv("AION_CLIENT_ID")
        self.secret = os.getenv("AION_SECRET")
        self._validate_configuration()
        self._client: Optional[Client] = None

    def _validate_configuration(self):
        """Validate that all required environment variables are set"""
        if not self.client_id or not self.secret:
            logger.error(
                "AION_CLIENT_ID and AION_SECRET environment variables must be set"
            )
            raise ValueError("AION_CLIENT_ID, AION_SECRET environment variables must be set")

    async def _build_transport(self) -> WebsocketsTransport:
        """
        Build a WebSocket transport with JWT authentication.

        Creates a new WebSocket transport configured with the current JWT token
        and appropriate connection settings from the API configuration.
        """
        aion_token = await aion_jwt_manager.get_token()
        url = (
            f"{aion_api_settings.ws_gql_url}"
            f"?token={aion_token}"
        )
        return WebsocketsTransport(url=url, ping_interval=aion_api_settings.keepalive)

    async def execute(self, query: str, variables: Optional[dict[str, Any]] = None) -> Any:
        """
        Execute a GraphQL query using a WebSocket connection.

        Establishes a fresh WebSocket connection, executes the provided GraphQL
        query with optional variables, and returns the result.
        """
        transport = await self._build_transport()
        async with Client(transport=transport, fetch_schema_from_transport=False) as session:
            return await session.execute(gql(query), variable_values=variables)

    async def subscribe(
            self, query: str, variables: Optional[dict[str, Any]] = None
    ) -> Any:
        """
        Subscribe to a GraphQL subscription and yield results.

        Establishes a persistent WebSocket connection and yields results from
        a GraphQL subscription as they arrive. The connection remains open
        until the async generator is closed.
        """
        transport = await self._build_transport()
        async with Client(transport=transport, fetch_schema_from_transport=False) as session:
            async for result in session.subscribe(gql(query), variable_values=variables):
                yield result

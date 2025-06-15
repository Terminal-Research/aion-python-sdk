"""Low level GraphQL client for communicating with the Aion API."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
import jwt
from gql import Client, gql
from gql.transport.websockets import WebsocketsTransport

from ..api_client.settings import settings

LOGGER = logging.getLogger(__name__)


@dataclass
class Token:
    """Represents an authentication token."""

    value: str
    expires_at: datetime

    @classmethod
    def from_jwt(cls, token: str) -> "Token":
        payload = jwt.decode(token, options={"verify_signature": False})
        exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        return cls(value=token, expires_at=exp)

    @property
    def expired(self) -> bool:
        return datetime.now(tz=timezone.utc) >= self.expires_at


class GqlClient:
    """Client for the Aion GraphQL API using websockets."""

    def __init__(self) -> None:
        self.client_id = os.getenv("AION_CLIENT_ID")
        self.secret = os.getenv("AION_SECRET")
        if not self.client_id or not self.secret:
            LOGGER.error(
                "AION_CLIENT_ID and AION_SECRET environment variables must be set"
            )
        self._token: Optional[Token] = None
        self._client: Optional[Client] = None

    def _request_token(self) -> Token:
        url = f"http://{settings.aion_api.host}:{settings.aion_api.port}/auth/token"
        payload = {"client_id": self.client_id, "secret_key": self.secret}
        response = httpx.post(url, json=payload, timeout=10)
        response.raise_for_status()
        token = response.json().get("access_token")
        return Token.from_jwt(token)

    def _ensure_token(self) -> None:
        if self._token is None or self._token.expired:
            self._token = self._request_token()

    def _build_transport(self) -> WebsocketsTransport:
        self._ensure_token()
        url = (
            f"ws://{settings.aion_api.host}:{settings.aion_api.port}/ws/graphql"
            f"?token={self._token.value}"
        )
        return WebsocketsTransport(url=url, ping_interval=settings.aion_api.keepalive)

    async def execute(self, query: str, variables: Optional[dict[str, Any]] = None) -> Any:
        """Execute a GraphQL query using a websocket connection."""
        transport = self._build_transport()
        async with Client(transport=transport, fetch_schema_from_transport=False) as session:
            return await session.execute(gql(query), variable_values=variables)

    async def subscribe(
        self, query: str, variables: Optional[dict[str, Any]] = None
    ) -> Any:
        """Subscribe to a GraphQL subscription."""
        transport = self._build_transport()
        async with Client(transport=transport, fetch_schema_from_transport=False) as session:
            async for result in session.subscribe(gql(query), variable_values=variables):
                yield result

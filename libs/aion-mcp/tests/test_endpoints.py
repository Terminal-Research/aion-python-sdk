"""Tests for remote Aion MCP endpoint helpers."""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from aion.api.exceptions import AionAuthenticationError
from aion.api.model_service_client import AION_PRINCIPAL_SELECTOR_HEADER
from aion.mcp import (
    AionMcpEndpoint,
    aion_control_plane_mcp_endpoint,
    aion_control_plane_mcp_endpoint_sync,
    aion_distribution_mcp_endpoint,
    aion_distribution_mcp_endpoint_sync,
    aion_mcp_authorization_headers,
)


class FakeAsyncTokenManager:
    """Async token manager that returns a fixed token."""

    def __init__(self, token: str | None) -> None:
        self.token = token

    async def get_token(self) -> str | None:
        """Return the configured token."""
        return self.token


class FakeSyncTokenManager:
    """Synchronous token manager that returns a fixed token."""

    def __init__(self, token: str | None) -> None:
        self.token = token

    def get_token_sync(self) -> str | None:
        """Return the configured token."""
        return self.token


def test_endpoint_returns_langchain_multi_server_config() -> None:
    endpoint = AionMcpEndpoint(
        name="aion_control_plane",
        url="https://api.example.com/mcp",
        headers={"Authorization": "Bearer jwt-token"},
    )

    assert endpoint.as_langchain_config() == {
        "transport": "http",
        "url": "https://api.example.com/mcp",
        "headers": {"Authorization": "Bearer jwt-token"},
    }
    assert endpoint.as_multi_server_config() == {
        "aion_control_plane": endpoint.as_langchain_config()
    }


def test_authorization_headers_require_a_token() -> None:
    with pytest.raises(AionAuthenticationError):
        aion_mcp_authorization_headers(None)


def test_authorization_headers_include_principal_selector() -> None:
    headers = aion_mcp_authorization_headers(
        "jwt-token",
        principal_selector="agent-environment:env-id",
    )

    assert headers == {
        "Authorization": "Bearer jwt-token",
        AION_PRINCIPAL_SELECTOR_HEADER: "agent-environment:env-id",
    }


def test_control_plane_endpoint_uses_async_token_manager() -> None:
    endpoint = asyncio.run(
        aion_control_plane_mcp_endpoint(
            jwt_manager=FakeAsyncTokenManager("jwt-token"),
            principal_selector="agent-environment:env-id",
            base_url="https://api.example.com/",
        )
    )

    assert endpoint.name == "aion_control_plane"
    assert endpoint.url == "https://api.example.com/mcp"
    assert endpoint.headers["Authorization"] == "Bearer jwt-token"
    assert (
        endpoint.headers[AION_PRINCIPAL_SELECTOR_HEADER]
        == "agent-environment:env-id"
    )


def test_control_plane_sync_endpoint_uses_sync_token_manager() -> None:
    endpoint = aion_control_plane_mcp_endpoint_sync(
        jwt_manager=FakeSyncTokenManager("jwt-token"),
        base_url="https://api.example.com/",
    )

    assert endpoint.url == "https://api.example.com/mcp"
    assert endpoint.headers == {"Authorization": "Bearer jwt-token"}


def test_distribution_endpoint_uses_default_twitter_capability() -> None:
    distribution_id = UUID("11111111-1111-1111-1111-111111111111")

    endpoint = asyncio.run(
        aion_distribution_mcp_endpoint(
            distribution_id,
            jwt_manager=FakeAsyncTokenManager("jwt-token"),
            base_url="https://api.example.com",
        )
    )

    assert endpoint.name == "twitter_distribution"
    assert endpoint.url == (
        "https://api.example.com/distributions/"
        "11111111-1111-1111-1111-111111111111"
        "/mcp/mcp.twitter.distribution"
    )
    assert endpoint.headers == {"Authorization": "Bearer jwt-token"}


def test_distribution_sync_endpoint_accepts_custom_capability() -> None:
    endpoint = aion_distribution_mcp_endpoint_sync(
        "distribution-id",
        capability_key="custom/key",
        jwt_manager=FakeSyncTokenManager("jwt-token"),
        base_url="https://api.example.com",
        name="custom_distribution",
    )

    assert endpoint.name == "custom_distribution"
    assert endpoint.url == (
        "https://api.example.com/distributions/distribution-id/mcp/custom%2Fkey"
    )

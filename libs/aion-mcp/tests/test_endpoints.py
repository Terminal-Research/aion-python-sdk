"""Tests for remote Aion MCP endpoint helpers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest

from aion.api.control_plane import (
    AION_CONTROL_PLANE_MCP_CAPABILITY_KEY,
    AION_PRINCIPAL_SELECTOR_HEADER,
    CapabilityKind,
    CapabilityReference,
    CapabilitySubjectSource,
    CapabilitySubject,
    PrincipalSelector,
    RuntimeCapabilityReference,
)
from aion.api.exceptions import AionAuthenticationError
from aion.mcp import (
    AionMcpEndpoint,
    aion_mcp_endpoint,
    aion_mcp_endpoint_sync,
    aion_mcp_authorization_headers,
    aion_runtime_context_mcp_endpoints_sync,
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


class FakeRuntimeContext:
    """Runtime context carrying an environment principal selector."""

    def get_environment(self) -> SimpleNamespace:
        """Return a fake environment."""
        return SimpleNamespace(id="env-id")

    def get_distribution(self) -> SimpleNamespace:
        """Return a fake incoming distribution."""
        return SimpleNamespace(id="distribution-id")

    def get_principal_selector(self) -> str:
        """Return the derived principal selector."""
        return "agent-environment:env-id"


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
        principal_selector=PrincipalSelector.agent_environment("env-id"),
    )

    assert headers == {
        "Authorization": "Bearer jwt-token",
        AION_PRINCIPAL_SELECTOR_HEADER: "agent-environment:env-id",
    }


def test_authorization_headers_accept_typed_principal_selector() -> None:
    headers = aion_mcp_authorization_headers(
        "jwt-token",
        principal_selector=PrincipalSelector.agent_environment("env-id"),
    )

    assert headers[AION_PRINCIPAL_SELECTOR_HEADER] == "agent-environment:env-id"


def test_control_plane_endpoint_uses_async_token_manager() -> None:
    endpoint = asyncio.run(
        aion_mcp_endpoint(
            CapabilityReference.global_mcp(),
            jwt_manager=FakeAsyncTokenManager("jwt-token"),
            principal_selector=PrincipalSelector.agent_environment("env-id"),
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
    endpoint = aion_mcp_endpoint_sync(
        CapabilityReference.global_mcp(),
        jwt_manager=FakeSyncTokenManager("jwt-token"),
        base_url="https://api.example.com/",
    )

    assert endpoint.url == "https://api.example.com/mcp"
    assert endpoint.headers == {"Authorization": "Bearer jwt-token"}


def test_generic_mcp_endpoint_accepts_capability_reference() -> None:
    """Verify the generic SDK entry point builds reference-based MCP URLs."""
    endpoint = aion_mcp_endpoint_sync(
        CapabilityReference.primary_mcp(
            CapabilitySubject.distribution("distribution-id")
        ),
        jwt_manager=FakeSyncTokenManager("jwt-token"),
        base_url="https://api.example.com/",
    )

    assert endpoint.name == "aion_distribution_distribution_id_mcp_primary"
    assert endpoint.url == "https://api.example.com/distributions/distribution-id/mcp"


def test_generic_mcp_endpoint_accepts_keyed_system_reference() -> None:
    """Verify subjectless keyed MCP references address system capabilities."""
    endpoint = aion_mcp_endpoint_sync(
        CapabilityReference.global_mcp(
            key=AION_CONTROL_PLANE_MCP_CAPABILITY_KEY
        ),
        jwt_manager=FakeSyncTokenManager("jwt-token"),
        base_url="https://api.example.com/",
    )

    assert endpoint.name == "aion_control_plane"
    assert endpoint.url == (
        "https://api.example.com/mcp/mcp.aion.control_plane"
    )


def test_generic_mcp_endpoint_rejects_non_mcp_references() -> None:
    """Verify A2A references cannot be used as MCP server endpoints."""
    with pytest.raises(ValueError, match="MCP capability reference"):
        aion_mcp_endpoint_sync(
            CapabilityReference(
                kind=CapabilityKind.A2A_ENDPOINT,
                subject=CapabilitySubject.distribution("distribution-id"),
            ),
            jwt_manager=FakeSyncTokenManager("jwt-token"),
            base_url="https://api.example.com/",
        )


def test_generic_mcp_endpoint_addresses_distribution_capability() -> None:
    distribution_id = UUID("11111111-1111-1111-1111-111111111111")

    endpoint = asyncio.run(
        aion_mcp_endpoint(
            CapabilityReference.mcp(
                CapabilitySubject.distribution(str(distribution_id)),
                key="mcp.twitter.distribution",
            ),
            jwt_manager=FakeAsyncTokenManager("jwt-token"),
            base_url="https://api.example.com",
            name="twitter_distribution",
        )
    )

    assert endpoint.name == "twitter_distribution"
    assert endpoint.url == (
        "https://api.example.com/distributions/"
        "11111111-1111-1111-1111-111111111111"
        "/mcp/mcp.twitter.distribution"
    )
    assert endpoint.headers == {"Authorization": "Bearer jwt-token"}


def test_generic_mcp_sync_endpoint_accepts_custom_name() -> None:
    endpoint = aion_mcp_endpoint_sync(
        CapabilityReference.mcp(
            CapabilitySubject.distribution("distribution-id"),
            key="custom/key",
        ),
        jwt_manager=FakeSyncTokenManager("jwt-token"),
        base_url="https://api.example.com",
        name="custom_distribution",
    )

    assert endpoint.name == "custom_distribution"
    assert endpoint.url == (
        "https://api.example.com/distributions/distribution-id/"
        "mcp/custom%2Fkey"
    )


def test_generic_mcp_sync_endpoint_addresses_environment_capability() -> None:
    endpoint = aion_mcp_endpoint_sync(
        CapabilityReference.mcp(
            CapabilitySubject.environment("env-id"),
            key="mcp.twitter.distribution",
        ),
        jwt_manager=FakeSyncTokenManager("jwt-token"),
        principal_selector=PrincipalSelector.agent_environment("env-id"),
        base_url="https://api.example.com",
        name="runtime_twitter",
    )

    assert endpoint.name == "runtime_twitter"
    assert endpoint.url == (
        "https://api.example.com/environments/env-id/"
        "mcp/mcp.twitter.distribution"
    )
    assert endpoint.headers == {
        "Authorization": "Bearer jwt-token",
        AION_PRINCIPAL_SELECTOR_HEADER: "agent-environment:env-id",
    }


def test_runtime_context_sync_endpoints_use_global_reference_and_capability() -> None:
    endpoints = aion_runtime_context_mcp_endpoints_sync(
        FakeRuntimeContext(),
        capability_references=[CapabilityReference.global_mcp()],
        runtime_capability_references=[
            RuntimeCapabilityReference.mcp(key="mcp.twitter.distribution")
        ],
        jwt_manager=FakeSyncTokenManager("jwt-token"),
        base_url="https://api.example.com",
    )

    assert [endpoint.name for endpoint in endpoints] == [
        "aion_control_plane",
        "aion_environment_env_id_mcp_twitter_distribution",
    ]
    assert [endpoint.url for endpoint in endpoints] == [
        "https://api.example.com/mcp",
        "https://api.example.com/environments/env-id/"
        "mcp/mcp.twitter.distribution",
    ]
    assert all(
        endpoint.headers[AION_PRINCIPAL_SELECTOR_HEADER]
        == "agent-environment:env-id"
        for endpoint in endpoints
    )


def test_runtime_context_sync_endpoints_accept_explicit_references() -> None:
    """Verify callers can address primary capabilities on arbitrary subjects."""
    endpoints = aion_runtime_context_mcp_endpoints_sync(
        FakeRuntimeContext(),
        capability_references=[
            CapabilityReference.primary_mcp(
                CapabilitySubject.distribution("distribution-id")
            )
        ],
        jwt_manager=FakeSyncTokenManager("jwt-token"),
        base_url="https://api.example.com",
    )

    assert [endpoint.name for endpoint in endpoints] == [
        "aion_distribution_distribution_id_mcp_primary"
    ]
    assert [endpoint.url for endpoint in endpoints] == [
        "https://api.example.com/distributions/distribution-id/mcp"
    ]


def test_runtime_context_sync_endpoints_resolve_runtime_references() -> None:
    """Verify runtime templates resolve subjects after context is available."""
    endpoints = aion_runtime_context_mcp_endpoints_sync(
        FakeRuntimeContext(),
        capability_references=[CapabilityReference.global_mcp()],
        runtime_capability_references=[
            RuntimeCapabilityReference.primary_mcp(
                CapabilitySubjectSource.INCOMING_DISTRIBUTION
            )
        ],
        jwt_manager=FakeSyncTokenManager("jwt-token"),
        base_url="https://api.example.com",
    )

    assert [endpoint.name for endpoint in endpoints] == [
        "aion_control_plane",
        "aion_distribution_distribution_id_mcp_primary",
    ]
    assert [endpoint.url for endpoint in endpoints] == [
        "https://api.example.com/mcp",
        "https://api.example.com/distributions/distribution-id/mcp",
    ]

"""Tests for LangGraph MCP bindings."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aion.api.control_plane import (
    AION_PRINCIPAL_SELECTOR_HEADER,
    CapabilityReference,
    CapabilitySubjectSource,
    CapabilitySubject,
    RuntimeCapabilityReference,
)
from aion.langgraph.authoring.mcp import (
    aion_langgraph_mcp_server_config_sync,
    load_aion_mcp_tools,
)


class FakeAsyncTokenManager:
    """Async token manager that returns a fixed token."""

    async def get_token(self) -> str:
        """Return the configured token."""
        return "jwt-token"


class FakeSyncTokenManager:
    """Synchronous token manager that returns a fixed token."""

    def get_token_sync(self) -> str:
        """Return the configured token."""
        return "jwt-token"


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


def test_langgraph_mcp_server_config_sync_uses_runtime_context() -> None:
    """Verify LangGraph config includes control-plane and capability endpoints."""
    config = aion_langgraph_mcp_server_config_sync(
        FakeRuntimeContext(),
        capability_references=[CapabilityReference.global_mcp()],
        runtime_capability_references=[
            RuntimeCapabilityReference.mcp(key="mcp.twitter.distribution")
        ],
        jwt_manager=FakeSyncTokenManager(),
        base_url="https://api.example.test",
    )

    assert set(config.keys()) == {
        "aion_metatools",
        "aion_environment_env_id_mcp_twitter_distribution",
    }
    assert config["aion_metatools"]["url"] == (
        "https://api.example.test/mcp/capabilities/mcp.aion.metatools"
    )
    capability = config["aion_environment_env_id_mcp_twitter_distribution"]
    assert capability["transport"] == "http"
    assert capability["url"] == (
        "https://api.example.test/environments/env-id/"
        "mcp/capabilities/mcp.twitter.distribution"
    )
    assert (
        capability["headers"][AION_PRINCIPAL_SELECTOR_HEADER]
        == "agent-environment:env-id"
    )


def test_langgraph_mcp_server_config_sync_accepts_references() -> None:
    """Verify LangGraph bindings support explicit capability references."""
    config = aion_langgraph_mcp_server_config_sync(
        None,
        capability_references=[
            CapabilityReference.primary_mcp(
                CapabilitySubject.distribution("distribution-id")
            )
        ],
        jwt_manager=FakeSyncTokenManager(),
        base_url="https://api.example.test",
    )

    assert set(config.keys()) == {
        "aion_distribution_distribution_id_mcp_primary"
    }
    assert (
        config["aion_distribution_distribution_id_mcp_primary"]["url"]
        == "https://api.example.test/distributions/distribution-id/mcp"
    )


def test_langgraph_mcp_server_config_sync_resolves_runtime_references() -> None:
    """Verify LangGraph bindings can derive capability subjects at runtime."""
    config = aion_langgraph_mcp_server_config_sync(
        FakeRuntimeContext(),
        capability_references=[CapabilityReference.global_mcp()],
        runtime_capability_references=[
            RuntimeCapabilityReference.primary_mcp(
                CapabilitySubjectSource.INCOMING_DISTRIBUTION
            )
        ],
        jwt_manager=FakeSyncTokenManager(),
        base_url="https://api.example.test",
    )

    assert set(config.keys()) == {
        "aion_metatools",
        "aion_distribution_distribution_id_mcp_primary",
    }


@pytest.mark.asyncio
async def test_load_aion_mcp_tools_uses_client_factory() -> None:
    """Verify LangGraph tool loading delegates to MultiServerMCPClient shape."""
    captured = {}

    class FakeClient:
        def __init__(self, config):
            captured["config"] = config

        async def get_tools(self):
            return ["tool-a", "tool-b"]

    tools = await load_aion_mcp_tools(
        FakeRuntimeContext(),
        capability_references=[CapabilityReference.global_mcp()],
        runtime_capability_references=[
            RuntimeCapabilityReference.mcp(key="mcp.twitter.distribution")
        ],
        jwt_manager=FakeAsyncTokenManager(),
        base_url="https://api.example.test",
        client_factory=FakeClient,
    )

    assert tools == ["tool-a", "tool-b"]
    assert "aion_metatools" in captured["config"]

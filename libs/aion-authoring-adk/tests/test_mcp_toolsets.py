"""Tests for Google ADK MCP bindings."""

from __future__ import annotations

import asyncio
import sys
from types import ModuleType, SimpleNamespace

import pytest

from aion.api.control_plane import (
    CapabilityReference,
    CapabilitySubject,
    CapabilitySubjectSource,
    RuntimeCapabilityReference,
)
from aion.adk.mcp import (
    aion_adk_mcp_toolset,
    aion_adk_mcp_toolsets_sync,
    default_adk_runtime_context,
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


def install_fake_google_adk(monkeypatch):
    """Install minimal Google ADK modules for lazy-import tests."""
    google = ModuleType("google")
    google_adk = ModuleType("google.adk")
    tools = ModuleType("google.adk.tools")
    base_toolset = ModuleType("google.adk.tools.base_toolset")
    mcp_tool = ModuleType("google.adk.tools.mcp_tool")
    mcp_toolset = ModuleType("google.adk.tools.mcp_tool.mcp_toolset")
    session_manager = ModuleType("google.adk.tools.mcp_tool.mcp_session_manager")

    class BaseToolset:
        pass

    class StreamableHTTPConnectionParams:
        def __init__(self, *, url, headers):
            self.url = url
            self.headers = headers

    class McpToolset:
        def __init__(
            self,
            *,
            connection_params,
            tool_filter,
            tool_name_prefix,
            require_confirmation,
        ):
            self.connection_params = connection_params
            self.tool_filter = tool_filter
            self.tool_name_prefix = tool_name_prefix
            self.require_confirmation = require_confirmation

        async def get_tools(self, readonly_context=None):
            return [f"tool:{self.connection_params.url}"]

        async def close(self):
            self.closed = True

    base_toolset.BaseToolset = BaseToolset
    session_manager.StreamableHTTPConnectionParams = StreamableHTTPConnectionParams
    mcp_toolset.McpToolset = McpToolset
    mcp_tool.mcp_toolset = mcp_toolset
    mcp_tool.mcp_session_manager = session_manager

    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.adk", google_adk)
    monkeypatch.setitem(sys.modules, "google.adk.tools", tools)
    monkeypatch.setitem(sys.modules, "google.adk.tools.base_toolset", base_toolset)
    monkeypatch.setitem(sys.modules, "google.adk.tools.mcp_tool", mcp_tool)
    monkeypatch.setitem(
        sys.modules,
        "google.adk.tools.mcp_tool.mcp_toolset",
        mcp_toolset,
    )
    monkeypatch.setitem(
        sys.modules,
        "google.adk.tools.mcp_tool.mcp_session_manager",
        session_manager,
    )


def test_default_adk_runtime_context_reads_state() -> None:
    """Verify default context provider reads common ADK state keys."""
    runtime_context = FakeRuntimeContext()
    readonly_context = SimpleNamespace(state={"aion_runtime_context": runtime_context})

    assert default_adk_runtime_context(readonly_context) is runtime_context


def test_google_adk_mcp_import_smoke() -> None:
    """Verify adapter imports match installed google-adk when available."""
    pytest.importorskip("google.adk.tools.mcp_tool.mcp_toolset")
    pytest.importorskip("google.adk.tools.mcp_tool.mcp_session_manager")

    from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
    from google.adk.tools.mcp_tool.mcp_session_manager import (
        StreamableHTTPConnectionParams,
    )

    assert McpToolset is not None
    assert StreamableHTTPConnectionParams is not None


def test_aion_adk_mcp_toolsets_sync_create_remote_toolsets(monkeypatch) -> None:
    """Verify direct ADK MCP toolsets use streamable HTTP endpoint params."""
    install_fake_google_adk(monkeypatch)

    toolsets = aion_adk_mcp_toolsets_sync(
        FakeRuntimeContext(),
        capability_references=[CapabilityReference.global_mcp()],
        runtime_capability_references=[
            RuntimeCapabilityReference.mcp(key="mcp.twitter.distribution")
        ],
        jwt_manager=FakeSyncTokenManager(),
        base_url="https://api.example.test",
    )

    assert [toolset.connection_params.url for toolset in toolsets] == [
        "https://api.example.test/mcp",
        "https://api.example.test/environments/env-id/"
        "mcp/mcp.twitter.distribution",
    ]
    assert toolsets[0].connection_params.headers["Authorization"] == "Bearer jwt-token"


def test_aion_adk_mcp_toolsets_sync_accepts_references(monkeypatch) -> None:
    """Verify direct ADK MCP toolsets support capability references."""
    install_fake_google_adk(monkeypatch)

    toolsets = aion_adk_mcp_toolsets_sync(
        None,
        capability_references=[
            CapabilityReference.primary_mcp(
                CapabilitySubject.distribution("distribution-id")
            )
        ],
        jwt_manager=FakeSyncTokenManager(),
        base_url="https://api.example.test",
    )

    assert [toolset.connection_params.url for toolset in toolsets] == [
        "https://api.example.test/distributions/distribution-id/mcp"
    ]


def test_aion_adk_mcp_toolset_resolves_tools_at_runtime(monkeypatch) -> None:
    """Verify the custom ADK BaseToolset resolves endpoint tools dynamically."""
    install_fake_google_adk(monkeypatch)
    readonly_context = SimpleNamespace(
        state={"aion_runtime_context": FakeRuntimeContext()}
    )

    toolset = aion_adk_mcp_toolset(
        capability_references=[CapabilityReference.global_mcp()],
        runtime_capability_references=[
            RuntimeCapabilityReference.mcp(key="mcp.twitter.distribution"),
            RuntimeCapabilityReference.primary_mcp(
                CapabilitySubjectSource.INCOMING_DISTRIBUTION
            )
        ],
        jwt_manager=FakeAsyncTokenManager(),
        base_url="https://api.example.test",
    )
    tools = asyncio.run(toolset.get_tools(readonly_context))

    assert tools == [
        "tool:https://api.example.test/mcp",
        "tool:https://api.example.test/environments/env-id/"
        "mcp/mcp.twitter.distribution",
        "tool:https://api.example.test/distributions/distribution-id/mcp",
    ]

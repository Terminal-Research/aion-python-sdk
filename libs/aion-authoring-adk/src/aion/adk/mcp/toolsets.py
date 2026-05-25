"""Google ADK toolset helpers for Aion MCP endpoints."""

from __future__ import annotations

from collections.abc import Iterable
from inspect import isawaitable
from typing import TYPE_CHECKING, Any, Callable

from aion.api.control_plane import (
    CapabilityReference,
    PrincipalSelector,
    RuntimeCapabilityReference,
)
from aion.mcp import (
    AionMcpEndpoint,
    aion_mcp_endpoint,
    aion_mcp_endpoint_sync,
    aion_runtime_context_mcp_endpoints,
    aion_runtime_context_mcp_endpoints_sync,
)

if TYPE_CHECKING:
    from aion.core.runtime.context.models import AionRuntimeContext

RuntimeContextProvider = Callable[[Any | None], "AionRuntimeContext | None"]
"""Callable that extracts an Aion runtime context from ADK context."""


def aion_adk_mcp_toolset(
    *,
    capability_references: Iterable[CapabilityReference] = (),
    runtime_capability_references: Iterable[RuntimeCapabilityReference] = (),
    principal_selector: PrincipalSelector | None = None,
    jwt_manager: Any | None = None,
    base_url: str | None = None,
    context_provider: RuntimeContextProvider | None = None,
    tool_filter: Any | None = None,
    tool_name_prefix: str | None = None,
    require_confirmation: bool | Callable[..., bool] = False,
) -> Any:
    """Create a context-aware ADK ``BaseToolset`` for Aion MCP endpoints.

    Args:
        capability_references: Explicit MCP references to load. Use these when
            addressing primary capabilities or subjects derived from runtime
            data other than the active environment. Include
            ``CapabilityReference.global_mcp()`` when the global control-plane
            MCP server should be connected.
        runtime_capability_references: MCP reference templates resolved from
            ADK's runtime context when ``get_tools`` is called.
        principal_selector: Optional explicit principal selector.
        jwt_manager: Optional async JWT manager.
        base_url: Optional Aion API base URL.
        context_provider: Optional function that extracts ``AionRuntimeContext``
            from ADK's readonly context.
        tool_filter: Optional filter forwarded to ADK's ``McpToolset``.
        tool_name_prefix: Optional prefix forwarded to ADK's ``McpToolset``.
        require_confirmation: ADK confirmation policy forwarded to
            ``McpToolset``.

    Returns:
        A Google ADK ``BaseToolset`` that resolves Aion MCP tools at runtime.

    Raises:
        ImportError: If ``google-adk`` is not installed.
    """
    try:
        from google.adk.tools.base_toolset import BaseToolset
    except ImportError as exc:
        raise ImportError("aion_adk_mcp_toolset requires google-adk.") from exc

    provider = context_provider or default_adk_runtime_context
    references = tuple(capability_references)
    runtime_references = tuple(runtime_capability_references)

    class AionAdkMcpToolset(BaseToolset):
        """ADK toolset that resolves Aion MCP endpoints per invocation."""

        def __init__(self) -> None:
            self._toolsets: dict[tuple[Any, ...], Any] = {}

        async def get_tools(self, readonly_context: Any | None = None) -> list[Any]:
            """Return ADK tools available to the current Aion runtime context."""
            context = provider(readonly_context)
            endpoints = await _endpoints(
                context,
                capability_references=references,
                runtime_capability_references=runtime_references,
                principal_selector=principal_selector,
                jwt_manager=jwt_manager,
                base_url=base_url,
            )
            tools: list[Any] = []
            for endpoint in endpoints:
                toolset = self._toolset_for(endpoint)
                result = toolset.get_tools(readonly_context)
                if isawaitable(result):
                    result = await result
                tools.extend(result)
            return tools

        async def close(self) -> None:
            """Close cached ADK MCP toolsets when they expose a close method."""
            for toolset in self._toolsets.values():
                close = getattr(toolset, "close", None)
                if close is None:
                    continue
                result = close()
                if isawaitable(result):
                    await result

        def _toolset_for(self, endpoint: AionMcpEndpoint) -> Any:
            key = _endpoint_cache_key(endpoint)
            toolset = self._toolsets.get(key)
            if toolset is None:
                toolset = _adk_mcp_toolset_from_endpoint(
                    endpoint,
                    tool_filter=tool_filter,
                    tool_name_prefix=tool_name_prefix,
                    require_confirmation=require_confirmation,
                )
                self._toolsets[key] = toolset
            return toolset

    return AionAdkMcpToolset()


def aion_adk_mcp_toolsets_sync(
    context: AionRuntimeContext | None = None,
    *,
    capability_references: Iterable[CapabilityReference] = (),
    runtime_capability_references: Iterable[RuntimeCapabilityReference] = (),
    principal_selector: PrincipalSelector | None = None,
    jwt_manager: Any | None = None,
    base_url: str | None = None,
    tool_filter: Any | None = None,
    tool_name_prefix: str | None = None,
    require_confirmation: bool | Callable[..., bool] = False,
) -> list[Any]:
    """Create direct ADK ``McpToolset`` instances for Aion endpoints.

    Args:
        context: Optional Aion runtime context. Required when runtime
            capability references are supplied.
        capability_references: Explicit MCP references to load. Use these when
            addressing primary capabilities or subjects derived from runtime
            data other than the active environment. Include
            ``CapabilityReference.global_mcp()`` when the global control-plane
            MCP server should be connected.
        runtime_capability_references: MCP reference templates resolved from
            ``context`` after the runtime subject is known.
        principal_selector: Optional explicit principal selector.
        jwt_manager: Optional synchronous JWT manager.
        base_url: Optional Aion API base URL.
        tool_filter: Optional filter forwarded to ADK's ``McpToolset``.
        tool_name_prefix: Optional prefix forwarded to ADK's ``McpToolset``.
        require_confirmation: ADK confirmation policy forwarded to
            ``McpToolset``.

    Returns:
        ADK ``McpToolset`` instances for the resolved endpoints.

    Raises:
        ImportError: If ``google-adk`` is not installed.
    """
    endpoints = _endpoints_sync(
        context,
        capability_references=capability_references,
        runtime_capability_references=runtime_capability_references,
        principal_selector=principal_selector,
        jwt_manager=jwt_manager,
        base_url=base_url,
    )
    return [
        _adk_mcp_toolset_from_endpoint(
            endpoint,
            tool_filter=tool_filter,
            tool_name_prefix=tool_name_prefix,
            require_confirmation=require_confirmation,
        )
        for endpoint in endpoints
    ]


def default_adk_runtime_context(readonly_context: Any | None) -> Any | None:
    """Extract an Aion runtime context from common ADK context locations.

    Args:
        readonly_context: ADK readonly context supplied to
            ``BaseToolset.get_tools``.

    Returns:
        An Aion runtime context when one is available, otherwise ``None``.
    """
    if readonly_context is None:
        return None
    for attribute in ("aion_runtime_context", "runtime_context"):
        context = getattr(readonly_context, attribute, None)
        if context is not None:
            return context
    state = getattr(readonly_context, "state", None)
    if state is None:
        return None
    if isinstance(state, dict):
        return state.get("aion_runtime_context") or state.get("runtime_context")
    getter = getattr(state, "get", None)
    if getter is None:
        return None
    return getter("aion_runtime_context") or getter("runtime_context")


async def _endpoints(
    context: AionRuntimeContext | None,
    *,
    capability_references: Iterable[CapabilityReference],
    runtime_capability_references: Iterable[RuntimeCapabilityReference],
    principal_selector: PrincipalSelector | None,
    jwt_manager: Any | None,
    base_url: str | None,
) -> list[AionMcpEndpoint]:
    references = tuple(capability_references)
    runtime_references = tuple(runtime_capability_references)
    if context is not None:
        return await aion_runtime_context_mcp_endpoints(
            context,
            capability_references=references,
            runtime_capability_references=runtime_references,
            principal_selector=principal_selector,
            jwt_manager=jwt_manager,
            base_url=base_url,
        )
    if runtime_references:
        raise ValueError("runtime context is required for runtime MCP references")
    endpoints = [
        await aion_mcp_endpoint(
            reference,
            jwt_manager=jwt_manager,
            principal_selector=principal_selector,
            base_url=base_url,
        )
        for reference in references
    ]
    return endpoints


def _endpoints_sync(
    context: AionRuntimeContext | None,
    *,
    capability_references: Iterable[CapabilityReference],
    runtime_capability_references: Iterable[RuntimeCapabilityReference],
    principal_selector: PrincipalSelector | None,
    jwt_manager: Any | None,
    base_url: str | None,
) -> list[AionMcpEndpoint]:
    references = tuple(capability_references)
    runtime_references = tuple(runtime_capability_references)
    if context is not None:
        return aion_runtime_context_mcp_endpoints_sync(
            context,
            capability_references=references,
            runtime_capability_references=runtime_references,
            principal_selector=principal_selector,
            jwt_manager=jwt_manager,
            base_url=base_url,
        )
    if runtime_references:
        raise ValueError("runtime context is required for runtime MCP references")
    endpoints = [
        aion_mcp_endpoint_sync(
            reference,
            jwt_manager=jwt_manager,
            principal_selector=principal_selector,
            base_url=base_url,
        )
        for reference in references
    ]
    return endpoints


def _adk_mcp_toolset_from_endpoint(
    endpoint: AionMcpEndpoint,
    *,
    tool_filter: Any | None,
    tool_name_prefix: str | None,
    require_confirmation: bool | Callable[..., bool],
) -> Any:
    try:
        from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
        from google.adk.tools.mcp_tool.mcp_session_manager import (
            StreamableHTTPConnectionParams,
        )
    except ImportError as exc:
        raise ImportError("Aion ADK MCP helpers require google-adk.") from exc

    return McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=endpoint.url,
            headers=dict(endpoint.headers),
        ),
        tool_filter=tool_filter,
        tool_name_prefix=tool_name_prefix,
        require_confirmation=require_confirmation,
    )


def _endpoint_cache_key(endpoint: AionMcpEndpoint) -> tuple[Any, ...]:
    return (
        endpoint.name,
        endpoint.url,
        endpoint.transport,
        tuple(sorted(endpoint.headers.items())),
    )


__all__ = [
    "aion_adk_mcp_toolset",
    "aion_adk_mcp_toolsets_sync",
    "default_adk_runtime_context",
]

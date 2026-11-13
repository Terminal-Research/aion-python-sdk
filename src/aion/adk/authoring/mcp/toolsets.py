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
from aion.mcp.endpoints import aion_mcp_http_client
from aion.api.callback_errors import reraise_callback_error
from aion.core.runtime.context.registry import AionRuntimeContextRegistry

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
            ``CapabilityReference.global_mcp()`` when the global metatools MCP
            server should be connected.
        runtime_capability_references: MCP reference templates resolved from
            ADK's runtime context when ``get_tools`` is called.
        principal_selector: Retired override; any explicit value is rejected.
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
                try:
                    result = toolset.get_tools(readonly_context)
                    if isawaitable(result):
                        result = await result
                except Exception as error:
                    reraise_callback_error(error)
                    raise
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
            ``CapabilityReference.global_mcp()`` when the global metatools MCP
            server should be connected.
        runtime_capability_references: MCP reference templates resolved from
            ``context`` after the runtime subject is known.
        principal_selector: Retired override; any explicit value is rejected.
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
    """The Aion runtime context of the turn an ADK context object belongs to.

    ADK hands ``BaseToolset.get_tools`` a ``ReadonlyContext`` over the
    invocation. The server keeps the turn's runtime context on its invocation
    context (``AionInvocationContext.aion_runtime_context``), which
    ``ReadonlyContext`` does not expose, so it is read from the invocation
    behind the object. A context object that carries the runtime context
    itself, or in its state, is read from there first; and when the object
    carries none at all, the context of the turn running here is used, which
    the server installs for every turn.

    Args:
        readonly_context: ADK readonly context supplied to
            ``BaseToolset.get_tools``.

    Returns:
        An Aion runtime context when one is available, otherwise ``None``.
    """
    if readonly_context is not None:
        invocation = getattr(readonly_context, "_invocation_context", None)
        for holder in (readonly_context, invocation):
            for attribute in ("aion_runtime_context", "runtime_context"):
                context = getattr(holder, attribute, None)
                if context is not None:
                    return context
        context = _from_state(getattr(readonly_context, "state", None))
        if context is not None:
            return context
    return AionRuntimeContextRegistry.get_current_context()


def _from_state(state: Any | None) -> Any | None:
    """A runtime context kept in ADK state under one of its usual keys, or None."""
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
            httpx_client_factory=aion_mcp_http_client,
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

"""LangGraph helpers for loading Aion MCP tools at runtime."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
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

ClientFactory = Callable[[dict[str, dict[str, Any]]], Any]
"""Factory that creates a LangChain MCP client from server config."""


@dataclass(frozen=True)
class AionLangGraphMcpResolver:
    """Resolve Aion MCP tools for LangGraph after runtime context is known."""

    capability_references: tuple[CapabilityReference, ...] = field(
        default_factory=tuple
    )
    """Explicit MCP references to load, including global control plane."""
    runtime_capability_references: tuple[
        RuntimeCapabilityReference, ...
    ] = field(
        default_factory=tuple
    )
    """MCP reference templates resolved after runtime context is available."""
    principal_selector: PrincipalSelector | None = None
    """Optional explicit principal selector for all MCP endpoints."""
    base_url: str | None = None
    """Optional Aion API base URL. Defaults to ``AION_API_HOST``."""
    jwt_manager: Any | None = None
    """Optional async JWT manager used by async tool loading."""
    sync_jwt_manager: Any | None = None
    """Optional synchronous JWT manager used by sync client construction."""

    async def server_config(
        self,
        context: AionRuntimeContext | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Return a ``MultiServerMCPClient`` config for the runtime context.

        Args:
            context: Optional Aion runtime context. Required when runtime
                capability references are configured.

        Returns:
            A LangChain MCP adapter server configuration.
        """
        return await aion_langgraph_mcp_server_config(
            context,
            capability_references=self.capability_references,
            runtime_capability_references=self.runtime_capability_references,
            principal_selector=self.principal_selector,
            jwt_manager=self.jwt_manager,
            base_url=self.base_url,
        )

    def server_config_sync(
        self,
        context: AionRuntimeContext | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Return a synchronous ``MultiServerMCPClient`` config.

        Args:
            context: Optional Aion runtime context. Required when runtime
                capability references are configured.

        Returns:
            A LangChain MCP adapter server configuration.
        """
        return aion_langgraph_mcp_server_config_sync(
            context,
            capability_references=self.capability_references,
            runtime_capability_references=self.runtime_capability_references,
            principal_selector=self.principal_selector,
            jwt_manager=self.sync_jwt_manager,
            base_url=self.base_url,
        )

    async def load_tools(
        self,
        context: AionRuntimeContext | None = None,
        *,
        client_factory: ClientFactory | None = None,
    ) -> list[Any]:
        """Load LangChain tools from the resolved Aion MCP endpoints.

        Args:
            context: Optional Aion runtime context. Required when runtime
                capability references are configured.
            client_factory: Optional test or customization hook for creating
                the MCP client.

        Returns:
            LangChain-compatible tools returned by ``MultiServerMCPClient``.
        """
        return await load_aion_mcp_tools(
            context,
            capability_references=self.capability_references,
            runtime_capability_references=self.runtime_capability_references,
            principal_selector=self.principal_selector,
            jwt_manager=self.jwt_manager,
            base_url=self.base_url,
            client_factory=client_factory,
        )

    def client(
        self,
        context: AionRuntimeContext | None = None,
        *,
        client_factory: ClientFactory | None = None,
    ) -> Any:
        """Create a ``MultiServerMCPClient`` from synchronous endpoint config.

        Args:
            context: Optional Aion runtime context. Required when runtime
                capability references are configured.
            client_factory: Optional test or customization hook for creating
                the MCP client.

        Returns:
            A LangChain MCP client instance.
        """
        return aion_langgraph_mcp_client(
            context,
            capability_references=self.capability_references,
            runtime_capability_references=self.runtime_capability_references,
            principal_selector=self.principal_selector,
            jwt_manager=self.sync_jwt_manager,
            base_url=self.base_url,
            client_factory=client_factory,
        )


async def aion_langgraph_mcp_server_config(
    context: AionRuntimeContext | None = None,
    *,
    capability_references: Iterable[CapabilityReference] = (),
    runtime_capability_references: Iterable[RuntimeCapabilityReference] = (),
    principal_selector: PrincipalSelector | None = None,
    jwt_manager: Any | None = None,
    base_url: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Return LangChain MCP server config for Aion endpoints.

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
        principal_selector: Optional explicit principal selector.
        jwt_manager: Optional async JWT manager.
        base_url: Optional Aion API base URL.

    Returns:
        A ``MultiServerMCPClient``-ready server configuration.
    """
    endpoints = await _endpoints(
        context,
        capability_references=capability_references,
        runtime_capability_references=runtime_capability_references,
        principal_selector=principal_selector,
        jwt_manager=jwt_manager,
        base_url=base_url,
    )
    return _multi_server_config(endpoints)


def aion_langgraph_mcp_server_config_sync(
    context: AionRuntimeContext | None = None,
    *,
    capability_references: Iterable[CapabilityReference] = (),
    runtime_capability_references: Iterable[RuntimeCapabilityReference] = (),
    principal_selector: PrincipalSelector | None = None,
    jwt_manager: Any | None = None,
    base_url: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Return synchronous LangChain MCP server config for Aion endpoints.

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
        principal_selector: Optional explicit principal selector.
        jwt_manager: Optional synchronous JWT manager.
        base_url: Optional Aion API base URL.

    Returns:
        A ``MultiServerMCPClient``-ready server configuration.
    """
    endpoints = _endpoints_sync(
        context,
        capability_references=capability_references,
        runtime_capability_references=runtime_capability_references,
        principal_selector=principal_selector,
        jwt_manager=jwt_manager,
        base_url=base_url,
    )
    return _multi_server_config(endpoints)


def aion_langgraph_mcp_client(
    context: AionRuntimeContext | None = None,
    *,
    capability_references: Iterable[CapabilityReference] = (),
    runtime_capability_references: Iterable[RuntimeCapabilityReference] = (),
    principal_selector: PrincipalSelector | None = None,
    jwt_manager: Any | None = None,
    base_url: str | None = None,
    client_factory: ClientFactory | None = None,
) -> Any:
    """Create a LangChain ``MultiServerMCPClient`` for Aion endpoints.

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
        principal_selector: Optional explicit principal selector.
        jwt_manager: Optional synchronous JWT manager.
        base_url: Optional Aion API base URL.
        client_factory: Optional test or customization hook for creating the
            MCP client.

    Returns:
        A LangChain MCP client instance.
    """
    config = aion_langgraph_mcp_server_config_sync(
        context,
        capability_references=capability_references,
        runtime_capability_references=runtime_capability_references,
        principal_selector=principal_selector,
        jwt_manager=jwt_manager,
        base_url=base_url,
    )
    factory = client_factory or _default_multi_server_client
    return factory(config)


async def load_aion_mcp_tools(
    context: AionRuntimeContext | None = None,
    *,
    capability_references: Iterable[CapabilityReference] = (),
    runtime_capability_references: Iterable[RuntimeCapabilityReference] = (),
    principal_selector: PrincipalSelector | None = None,
    jwt_manager: Any | None = None,
    base_url: str | None = None,
    client_factory: ClientFactory | None = None,
) -> list[Any]:
    """Load LangChain tools from Aion MCP endpoints.

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
        principal_selector: Optional explicit principal selector.
        jwt_manager: Optional async JWT manager.
        base_url: Optional Aion API base URL.
        client_factory: Optional test or customization hook for creating the
            MCP client.

    Returns:
        LangChain-compatible tools returned by ``MultiServerMCPClient``.
    """
    config = await aion_langgraph_mcp_server_config(
        context,
        capability_references=capability_references,
        runtime_capability_references=runtime_capability_references,
        principal_selector=principal_selector,
        jwt_manager=jwt_manager,
        base_url=base_url,
    )
    factory = client_factory or _default_multi_server_client
    client = factory(config)
    return await client.get_tools()


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


def _multi_server_config(
    endpoints: Iterable[AionMcpEndpoint],
) -> dict[str, dict[str, Any]]:
    config: dict[str, dict[str, Any]] = {}
    for endpoint in endpoints:
        config.update(endpoint.as_multi_server_config())
    return config


def _default_multi_server_client(config: dict[str, dict[str, Any]]) -> Any:
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError as exc:
        raise ImportError(
            "Aion LangGraph MCP helpers require langchain-mcp-adapters."
        ) from exc
    return MultiServerMCPClient(config)


__all__ = [
    "AionLangGraphMcpResolver",
    "aion_langgraph_mcp_client",
    "aion_langgraph_mcp_server_config",
    "aion_langgraph_mcp_server_config_sync",
    "load_aion_mcp_tools",
]

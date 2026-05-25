"""Remote Aion MCP endpoint helpers."""

from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from typing import Any, Protocol

from aion.api.control_plane import (
    AION_CONTROL_PLANE_MCP_CAPABILITY_KEY,
    AionControlPlanePaths,
    CapabilityKind,
    CapabilityReference,
    PrincipalSelector,
    RuntimeCapabilityReference,
)
from aion.api.exceptions import AionAuthenticationError
from aion.api.http import aion_jwt_manager

if TYPE_CHECKING:
    from aion.core.runtime.context.models import AionRuntimeContext

class AsyncTokenManager(Protocol):
    """Async token provider used by remote Aion MCP endpoint builders."""

    async def get_token(self) -> str | None:
        """Return a current Aion bearer token, if authentication succeeds."""


class SyncTokenManager(Protocol):
    """Synchronous token provider used by remote Aion MCP endpoint builders."""

    def get_token_sync(self) -> str | None:
        """Return a current Aion bearer token, if authentication succeeds."""


@dataclass(frozen=True)
class AionMcpEndpoint:
    """Connection information for an Aion remote MCP server.

    The endpoint object is intentionally framework-neutral. Use
    :meth:`as_langchain_config` with ``langchain-mcp-adapters`` and wrap it in
    a mapping keyed by :attr:`name` before passing it to
    ``MultiServerMCPClient``.

    Attributes:
        name: Stable server name for multi-server MCP client maps.
        url: Absolute MCP server URL.
        headers: HTTP headers to attach to MCP requests.
        transport: Transport name expected by the MCP adapter. LangChain MCP
            adapters use ``"http"`` for streamable HTTP MCP servers.
    """

    name: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    transport: str = "http"

    def as_langchain_config(self) -> dict[str, Any]:
        """Return a server config for ``MultiServerMCPClient``.

        Returns:
            A dictionary with the ``transport``, ``url``, and ``headers`` keys
            expected by LangChain's MCP adapter.
        """
        config: dict[str, Any] = {
            "transport": self.transport,
            "url": self.url,
        }
        if self.headers:
            config["headers"] = dict(self.headers)
        return config

    def as_multi_server_config(self) -> dict[str, dict[str, Any]]:
        """Return this endpoint keyed by :attr:`name` for MCP clients.

        Returns:
            A ``MultiServerMCPClient``-ready mapping containing this endpoint.
        """
        return {self.name: self.as_langchain_config()}


def aion_mcp_authorization_headers(
    token: str | None,
    *,
    principal_selector: PrincipalSelector | None = None,
) -> dict[str, str]:
    """Build authenticated headers for remote Aion MCP requests.

    Args:
        token: Aion JWT used as the bearer token.
        principal_selector: Optional runtime principal selector to scope MCP
            authorization to an agent identity or environment.

    Returns:
        HTTP headers for Aion MCP requests.

    Raises:
        AionAuthenticationError: If no token is available.
    """
    if not token:
        raise AionAuthenticationError("Unable to obtain an Aion API token.")

    headers = {"Authorization": f"Bearer {token}"}
    if principal_selector:
        headers.update(principal_selector.to_headers())
    return headers


async def aion_mcp_endpoint(
    reference: CapabilityReference | None = None,
    *,
    jwt_manager: AsyncTokenManager | None = None,
    principal_selector: PrincipalSelector | None = None,
    base_url: str | None = None,
    name: str | None = None,
) -> AionMcpEndpoint:
    """Create an authenticated endpoint for an Aion MCP capability reference.

    Args:
        reference: MCP capability reference to address. ``None`` selects the
            global Aion control-plane MCP server.
        jwt_manager: Optional async token manager. Defaults to the SDK's
            global refreshing JWT manager.
        principal_selector: Optional runtime principal selector header.
        base_url: Optional API base URL. Defaults to ``AION_API_HOST``.
        name: Optional server name used in multi-server MCP client maps.

    Returns:
        Authenticated endpoint metadata for the referenced MCP server.

    Raises:
        ValueError: If ``reference`` does not address an MCP server.
    """
    normalized = _mcp_reference(reference)
    manager = jwt_manager or aion_jwt_manager
    token = await manager.get_token()
    return _reference_endpoint(
        normalized,
        token=token,
        principal_selector=principal_selector,
        base_url=base_url,
        name=name,
    )


def aion_mcp_endpoint_sync(
    reference: CapabilityReference | None = None,
    *,
    jwt_manager: SyncTokenManager | None = None,
    principal_selector: PrincipalSelector | None = None,
    base_url: str | None = None,
    name: str | None = None,
) -> AionMcpEndpoint:
    """Create an authenticated Aion MCP endpoint synchronously.

    Args:
        reference: MCP capability reference to address. ``None`` selects the
            global Aion control-plane MCP server.
        jwt_manager: Optional synchronous token manager. Defaults to the SDK's
            global refreshing JWT manager.
        principal_selector: Optional runtime principal selector header.
        base_url: Optional API base URL. Defaults to ``AION_API_HOST``.
        name: Optional server name used in multi-server MCP client maps.

    Returns:
        Authenticated endpoint metadata for the referenced MCP server.

    Raises:
        ValueError: If ``reference`` does not address an MCP server.
    """
    normalized = _mcp_reference(reference)
    manager = jwt_manager or aion_jwt_manager
    token = manager.get_token_sync()
    return _reference_endpoint(
        normalized,
        token=token,
        principal_selector=principal_selector,
        base_url=base_url,
        name=name,
    )


async def aion_runtime_context_mcp_endpoints(
    context: AionRuntimeContext,
    *,
    capability_references: Iterable[CapabilityReference] = (),
    runtime_capability_references: Iterable[RuntimeCapabilityReference] = (),
    principal_selector: PrincipalSelector | None = None,
    jwt_manager: AsyncTokenManager | None = None,
    base_url: str | None = None,
) -> list[AionMcpEndpoint]:
    """Create MCP endpoints available from a runtime context.

    Args:
        context: Current Aion runtime context.
        capability_references: Explicit MCP capability references. Use this
            for primary subject selection, distribution-scoped MCP, or any
            subject that should not default to the active environment. Include
            ``CapabilityReference.global_mcp()`` here when the global
            control-plane MCP server should be connected.
        runtime_capability_references: Reference templates to resolve from
            ``context`` after the runtime subject is known.
        principal_selector: Optional explicit principal selector. When omitted,
            the selector is derived from ``context``.
        jwt_manager: Optional async token manager. Defaults to the SDK's
            global refreshing JWT manager.
        base_url: Optional API base URL. Defaults to ``AION_API_HOST``.

    Returns:
        Authenticated endpoint metadata for requested MCP servers.
    """
    manager = jwt_manager or aion_jwt_manager
    token = await manager.get_token()
    return _runtime_context_endpoints(
        context,
        capability_references=capability_references,
        runtime_capability_references=runtime_capability_references,
        principal_selector=principal_selector,
        token=token,
        base_url=base_url,
    )


def aion_runtime_context_mcp_endpoints_sync(
    context: AionRuntimeContext,
    *,
    capability_references: Iterable[CapabilityReference] = (),
    runtime_capability_references: Iterable[RuntimeCapabilityReference] = (),
    principal_selector: PrincipalSelector | None = None,
    jwt_manager: SyncTokenManager | None = None,
    base_url: str | None = None,
) -> list[AionMcpEndpoint]:
    """Create MCP endpoints from a runtime context synchronously.

    Args:
        context: Current Aion runtime context.
        capability_references: Explicit MCP capability references. Use this
            for primary subject selection, distribution-scoped MCP, or any
            subject that should not default to the active environment. Include
            ``CapabilityReference.global_mcp()`` here when the global
            control-plane MCP server should be connected.
        runtime_capability_references: Reference templates to resolve from
            ``context`` after the runtime subject is known.
        principal_selector: Optional explicit principal selector. When omitted,
            the selector is derived from ``context``.
        jwt_manager: Optional synchronous token manager. Defaults to the SDK's
            global refreshing JWT manager.
        base_url: Optional API base URL. Defaults to ``AION_API_HOST``.

    Returns:
        Authenticated endpoint metadata for requested MCP servers.
    """
    manager = jwt_manager or aion_jwt_manager
    token = manager.get_token_sync()
    return _runtime_context_endpoints(
        context,
        capability_references=capability_references,
        runtime_capability_references=runtime_capability_references,
        principal_selector=principal_selector,
        token=token,
        base_url=base_url,
    )


def _runtime_context_endpoints(
    context: AionRuntimeContext,
    *,
    capability_references: Iterable[CapabilityReference],
    runtime_capability_references: Iterable[RuntimeCapabilityReference],
    principal_selector: PrincipalSelector | None,
    token: str | None,
    base_url: str | None,
) -> list[AionMcpEndpoint]:
    principal_selector = principal_selector or _principal_selector_from_context(
        context
    )
    headers = aion_mcp_authorization_headers(
        token,
        principal_selector=principal_selector,
    )
    paths = AionControlPlanePaths(base_url)
    endpoints: list[AionMcpEndpoint] = []

    references = [_mcp_reference(item) for item in capability_references]
    runtime_references: list[CapabilityReference] = []
    for template in runtime_capability_references:
        resolved = template.resolve(context)
        if resolved is not None:
            runtime_references.append(resolved)
    references.extend(_mcp_reference(item) for item in runtime_references)

    for reference in references:
        endpoints.append(
            AionMcpEndpoint(
                name=_capability_endpoint_name(reference),
                url=paths.capability_url(reference),
                headers=headers,
            )
        )
    return endpoints


def _reference_endpoint(
    reference: CapabilityReference,
    *,
    token: str | None,
    principal_selector: PrincipalSelector | None,
    base_url: str | None,
    name: str | None,
) -> AionMcpEndpoint:
    paths = AionControlPlanePaths(base_url)
    return AionMcpEndpoint(
        name=name or _capability_endpoint_name(reference),
        url=paths.capability_url(reference),
        headers=aion_mcp_authorization_headers(
            token,
            principal_selector=principal_selector,
        ),
    )


def _principal_selector_from_context(
    context: AionRuntimeContext,
) -> PrincipalSelector | None:
    raw_selector = context.get_principal_selector()
    if raw_selector:
        return PrincipalSelector.from_header_value(raw_selector)
    return PrincipalSelector.from_runtime_context(context)


def _mcp_reference(
    reference: CapabilityReference | None,
) -> CapabilityReference:
    normalized = (
        CapabilityReference.global_mcp() if reference is None else reference
    )
    if normalized.kind != CapabilityKind.MCP_SERVER:
        raise ValueError("Aion MCP endpoints require an MCP capability reference")
    return normalized


def _capability_endpoint_name(reference: CapabilityReference) -> str:
    if reference.subject is None and _is_control_plane_key(reference):
        return "aion_control_plane"
    if (
        reference.kind == CapabilityKind.MCP_SERVER
        and reference.subject is not None
        and reference.key.is_concrete
    ):
        return (
            f"aion_{reference.subject.server_name_fragment}_"
            f"{reference.key.server_name_fragment}"
        )
    return f"aion_{reference.server_name_fragment}"


def _is_control_plane_key(reference: CapabilityReference) -> bool:
    if reference.key.is_primary:
        return True
    return (
        reference.key.require_concrete()
        == AION_CONTROL_PLANE_MCP_CAPABILITY_KEY
    )


__all__ = [
    "AionMcpEndpoint",
    "aion_mcp_endpoint",
    "aion_mcp_endpoint_sync",
    "aion_mcp_authorization_headers",
    "aion_runtime_context_mcp_endpoints",
    "aion_runtime_context_mcp_endpoints_sync",
]

"""Remote Aion MCP endpoint helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import quote
from uuid import UUID

from aion.api.exceptions import AionAuthenticationError
from aion.api.http import aion_jwt_manager
from aion.api.model_service_client import AION_PRINCIPAL_SELECTOR_HEADER
from aion.core.settings import api_settings

DEFAULT_TWITTER_DISTRIBUTION_CAPABILITY_KEY = "mcp.twitter.distribution"
"""Default capability key for the Twitter distribution MCP server."""


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
    principal_selector: str | None = None,
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
        headers[AION_PRINCIPAL_SELECTOR_HEADER] = principal_selector
    return headers


async def aion_control_plane_mcp_endpoint(
    *,
    jwt_manager: AsyncTokenManager | None = None,
    principal_selector: str | None = None,
    base_url: str | None = None,
    name: str = "aion_control_plane",
) -> AionMcpEndpoint:
    """Create the authenticated Aion control-plane MCP endpoint.

    The control-plane MCP server exposes stable tools such as
    ``aion_tool_search`` and ``aion_tool_execute``. The bearer token comes from
    the configured Aion SDK client credentials.

    Args:
        jwt_manager: Optional async token manager. Defaults to the SDK's
            global refreshing JWT manager.
        principal_selector: Optional runtime principal selector header.
        base_url: Optional API base URL. Defaults to ``AION_API_HOST``.
        name: Server name used in multi-server MCP client maps.

    Returns:
        Authenticated endpoint metadata for the control-plane MCP server.
    """
    manager = jwt_manager or aion_jwt_manager
    token = await manager.get_token()
    return AionMcpEndpoint(
        name=name,
        url=f"{_api_base_url(base_url)}/mcp",
        headers=aion_mcp_authorization_headers(
            token,
            principal_selector=principal_selector,
        ),
    )


def aion_control_plane_mcp_endpoint_sync(
    *,
    jwt_manager: SyncTokenManager | None = None,
    principal_selector: str | None = None,
    base_url: str | None = None,
    name: str = "aion_control_plane",
) -> AionMcpEndpoint:
    """Create the authenticated Aion control-plane MCP endpoint synchronously.

    Args:
        jwt_manager: Optional synchronous token manager. Defaults to the SDK's
            global refreshing JWT manager.
        principal_selector: Optional runtime principal selector header.
        base_url: Optional API base URL. Defaults to ``AION_API_HOST``.
        name: Server name used in multi-server MCP client maps.

    Returns:
        Authenticated endpoint metadata for the control-plane MCP server.
    """
    manager = jwt_manager or aion_jwt_manager
    token = manager.get_token_sync()
    return AionMcpEndpoint(
        name=name,
        url=f"{_api_base_url(base_url)}/mcp",
        headers=aion_mcp_authorization_headers(
            token,
            principal_selector=principal_selector,
        ),
    )


async def aion_distribution_mcp_endpoint(
    distribution_id: str | UUID,
    *,
    capability_key: str = DEFAULT_TWITTER_DISTRIBUTION_CAPABILITY_KEY,
    jwt_manager: AsyncTokenManager | None = None,
    principal_selector: str | None = None,
    base_url: str | None = None,
    name: str = "twitter_distribution",
) -> AionMcpEndpoint:
    """Create an authenticated endpoint for a distribution MCP capability.

    Args:
        distribution_id: Distribution identifier for the MCP capability.
        capability_key: Capability key to address. Defaults to the Twitter
            distribution MCP capability.
        jwt_manager: Optional async token manager. Defaults to the SDK's
            global refreshing JWT manager.
        principal_selector: Optional runtime principal selector header.
        base_url: Optional API base URL. Defaults to ``AION_API_HOST``.
        name: Server name used in multi-server MCP client maps.

    Returns:
        Authenticated endpoint metadata for the distribution MCP server.
    """
    manager = jwt_manager or aion_jwt_manager
    token = await manager.get_token()
    return _distribution_endpoint(
        distribution_id,
        capability_key=capability_key,
        token=token,
        principal_selector=principal_selector,
        base_url=base_url,
        name=name,
    )


def aion_distribution_mcp_endpoint_sync(
    distribution_id: str | UUID,
    *,
    capability_key: str = DEFAULT_TWITTER_DISTRIBUTION_CAPABILITY_KEY,
    jwt_manager: SyncTokenManager | None = None,
    principal_selector: str | None = None,
    base_url: str | None = None,
    name: str = "twitter_distribution",
) -> AionMcpEndpoint:
    """Create an authenticated distribution MCP endpoint synchronously.

    Args:
        distribution_id: Distribution identifier for the MCP capability.
        capability_key: Capability key to address. Defaults to the Twitter
            distribution MCP capability.
        jwt_manager: Optional synchronous token manager. Defaults to the SDK's
            global refreshing JWT manager.
        principal_selector: Optional runtime principal selector header.
        base_url: Optional API base URL. Defaults to ``AION_API_HOST``.
        name: Server name used in multi-server MCP client maps.

    Returns:
        Authenticated endpoint metadata for the distribution MCP server.
    """
    manager = jwt_manager or aion_jwt_manager
    token = manager.get_token_sync()
    return _distribution_endpoint(
        distribution_id,
        capability_key=capability_key,
        token=token,
        principal_selector=principal_selector,
        base_url=base_url,
        name=name,
    )


def _distribution_endpoint(
    distribution_id: str | UUID,
    *,
    capability_key: str,
    token: str | None,
    principal_selector: str | None,
    base_url: str | None,
    name: str,
) -> AionMcpEndpoint:
    base = _api_base_url(base_url)
    distribution_path = _path_part(distribution_id)
    capability_path = _path_part(capability_key)
    return AionMcpEndpoint(
        name=name,
        url=f"{base}/distributions/{distribution_path}/mcp/{capability_path}",
        headers=aion_mcp_authorization_headers(
            token,
            principal_selector=principal_selector,
        ),
    )


def _api_base_url(base_url: str | None) -> str:
    return (base_url or api_settings.http_url).rstrip("/")


def _path_part(value: str | UUID) -> str:
    return quote(str(value), safe="")


__all__ = [
    "DEFAULT_TWITTER_DISTRIBUTION_CAPABILITY_KEY",
    "AionMcpEndpoint",
    "aion_control_plane_mcp_endpoint",
    "aion_control_plane_mcp_endpoint_sync",
    "aion_distribution_mcp_endpoint",
    "aion_distribution_mcp_endpoint_sync",
    "aion_mcp_authorization_headers",
]

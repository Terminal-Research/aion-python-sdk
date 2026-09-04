"""MCP proxy and remote endpoint utilities."""

from .endpoints import (
    AionMcpEndpoint,
    aion_mcp_endpoint,
    aion_mcp_endpoint_sync,
    aion_mcp_authorization_headers,
    aion_runtime_context_mcp_endpoints,
    aion_runtime_context_mcp_endpoints_sync,
)


def load_proxy(*args, **kwargs):
    """Load an ASGI MCP proxy from configuration.

    The proxy dependencies are imported lazily so endpoint-only integrations do
    not need to import ASGI proxy modules during package initialization.
    """
    from .proxy import load_proxy as _load_proxy

    return _load_proxy(*args, **kwargs)

__all__ = [
    "AionMcpEndpoint",
    "aion_mcp_endpoint",
    "aion_mcp_endpoint_sync",
    "aion_mcp_authorization_headers",
    "aion_runtime_context_mcp_endpoints",
    "aion_runtime_context_mcp_endpoints_sync",
    "load_proxy",
]

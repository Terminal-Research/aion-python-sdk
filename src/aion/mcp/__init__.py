"""MCP proxy and remote endpoint utilities."""

from aion.core.utils.optional_deps import is_own_module, missing_server_extra_error

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
    not need to import ASGI proxy modules during package initialization. That
    makes the call, rather than the import, the place a base install finds out
    it has no ASGI proxy library - so the call is where the extras are named.
    """
    try:
        from .proxy import load_proxy as _load_proxy
    except ModuleNotFoundError as exc:
        # An aion.* module missing here means an incomplete wheel, not an
        # absent extra: no install command fixes it, so it goes up as it is.
        if is_own_module(exc.name):
            raise
        raise missing_server_extra_error("aion.mcp.load_proxy", exc) from exc

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

"""
AION Agent Proxy Server Package
"""
from aion.core.utils.optional_deps import (
    MissingOptionalDependency,
    is_own_module,
    missing_server_extra_error,
)

# This package ships in every wheel; the ASGI proxy libraries and the HTTP
# server stack it runs on only arrive with a server extra. Without them the
# import fails deep inside a third-party module, on a name that means nothing
# to whoever wrote the agent.
try:
    from .server import AionAgentProxyServer
    from .client import ProxyHttpClient
    from .handlers import RequestHandler
    from .exceptions import (
        AgentNotFoundException,
        AgentUnavailableException,
        AgentTimeoutException,
        AgentProxyException
    )
    from .types import AgentHealthInfo, SystemHealthResponse
except MissingOptionalDependency as exc:
    # The proxy reads the server's settings, so aion.server's guard gets there
    # first. Same extras, so only the name changes: the reader imported
    # aion.proxy, and that is what the line says.
    raise missing_server_extra_error("aion.proxy", exc) from exc
except ModuleNotFoundError as exc:
    if is_own_module(exc.name):
        raise
    raise missing_server_extra_error("aion.proxy", exc) from exc

__all__ = [
    "AionAgentProxyServer",
    "ProxyHttpClient",
    "RequestHandler",
    "AgentNotFoundException",
    "AgentUnavailableException",
    "AgentTimeoutException",
    "AgentProxyException",
    "AgentHealthInfo",
    "SystemHealthResponse",
]

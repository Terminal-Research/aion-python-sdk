# aion-mcp

Utilities for Aion MCP integrations.

The library can:

- proxy a local MCP server configured in `aion.yaml`
- build authenticated remote MCP endpoint configs for Aion servers

## Remote endpoints

The remote endpoint helpers use the configured `AION_CLIENT_ID` and
`AION_CLIENT_SECRET` through the SDK JWT manager. Each returned endpoint
contains a bearer token header and can be passed to LangChain's MCP adapter.

```python
from langchain_mcp_adapters.client import MultiServerMCPClient
from aion.mcp import (
    aion_control_plane_mcp_endpoint,
    aion_distribution_mcp_endpoint,
)

control_plane = await aion_control_plane_mcp_endpoint(
    principal_selector="agent-environment:env-id",
)
twitter = await aion_distribution_mcp_endpoint(
    "distribution-id",
    principal_selector="agent-environment:env-id",
)

client = MultiServerMCPClient(
    control_plane.as_multi_server_config()
    | twitter.as_multi_server_config()
)
tools = await client.get_tools()
```

Use `aion_control_plane_mcp_endpoint_sync` and
`aion_distribution_mcp_endpoint_sync` from synchronous setup code.

The control-plane endpoint exposes stable tools such as `aion_tool_search`
and `aion_tool_execute`. The distribution endpoint addresses a concrete MCP
capability, defaulting to the Twitter distribution capability key.

## Local proxy

`load_proxy` reads `aion.yaml` using PyYAML and returns an ASGI proxy when
`aion.mcp.port` is configured.

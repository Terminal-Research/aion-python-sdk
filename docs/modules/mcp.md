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
from aion.api import CapabilityReference, CapabilitySubject, PrincipalSelector
from aion.mcp import aion_mcp_endpoint

principal = PrincipalSelector.agent_environment("env-id")
metatools = await aion_mcp_endpoint(
    CapabilityReference.global_mcp(),
    principal_selector=principal,
)
twitter = await aion_mcp_endpoint(
    CapabilityReference.mcp(
        CapabilitySubject.environment("env-id"),
        key="mcp.twitter.distribution",
    ),
    principal_selector=principal,
)
primary_distribution = await aion_mcp_endpoint(
    CapabilityReference.primary_mcp(
        CapabilitySubject.distribution("distribution-id")
    ),
    principal_selector=principal,
)

client = MultiServerMCPClient(
    metatools.as_multi_server_config()
    | twitter.as_multi_server_config()
    | primary_distribution.as_multi_server_config()
)
tools = await client.get_tools()
```

Use `aion_mcp_endpoint_sync` from synchronous setup code.

The metatools endpoint exposes stable tools such as `aion_tool_search`
and `aion_tool_execute`. Capability endpoints address concrete MCP servers
exposed by distribution, environment, or agent subjects. New code can use
`aion_mcp_endpoint` with a `CapabilityReference` when it wants SDK-level
addressing by subject, kind, and primary-or-concrete key selector. For runtime
code, `aion_runtime_context_mcp_endpoints` derives the principal selector from
`AionRuntimeContext`; pass `CapabilityReference.global_mcp()` in
`capability_references` when the global metatools MCP server should be
connected.

## Local proxy

`load_proxy` reads `aion.yaml` using PyYAML and returns an ASGI proxy when
`aion.mcp.port` is configured.

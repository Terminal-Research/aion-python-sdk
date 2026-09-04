# aion.adk.authoring

Google ADK authoring helpers for Aion MCP access. This subpackage contains the
framework-specific MCP toolset bindings that agent authors import directly,
alongside the Aion-backed Google ADK model helpers. The server-side plugin that
runs such an agent is `aion.adk.server`.

---

## Installation

```bash
pip install "aionto-sdk[adk-authoring]"
```

Or, if you are serving the agent with Aion:

```bash
pip install "aionto-sdk[adk-server]"
```

The server extra already includes this one — install `[adk-authoring]` on its
own only when something else runs the agent.

---

## MCP tools

Use `aion_adk_mcp_toolset` when ADK should resolve Aion MCP tools from the
current runtime context:

```python
from google.adk.agents import Agent
from aion.api import (
    CapabilityReference,
    CapabilitySubjectSource,
    RuntimeCapabilityReference,
)
from aion.adk.authoring import aion_adk_mcp_toolset, aion_lite_llm

agent = Agent(
    name="research_agent",
    model=aion_lite_llm("model-id-from-control-plane"),
    tools=[
        aion_adk_mcp_toolset(
            capability_references=[
                CapabilityReference.global_mcp(),
            ],
            runtime_capability_references=[
                RuntimeCapabilityReference.primary_mcp(
                    CapabilitySubjectSource.INCOMING_DISTRIBUTION
                )
            ],
        )
    ],
)
```

The toolset implements ADK's `BaseToolset.get_tools(readonly_context)` path and
derives Aion MCP URLs, bearer auth, and principal selector headers at runtime.
Use `capability_references` for explicit SDK-level subject + kind + key
references. Use `runtime_capability_references` when the subject must be
resolved from the current request.

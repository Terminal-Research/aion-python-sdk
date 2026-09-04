# aion-authoring-adk

Google ADK authoring helpers for Aion MCP access. This package contains the
framework-specific MCP toolset bindings that agent authors import directly.

The existing `aion-adk` package contains Aion-backed Google ADK model helpers.
The Aion Server control-plane plugin lives in `aion-plugin-adk`.

---

## Installation

```bash
pip install aion-authoring-adk
```

Or, when installing through the umbrella SDK package:

```bash
pip install "aion-sdk[adk-authoring]"
```

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
from aion.adk.mcp import aion_adk_mcp_toolset
from aion.adk.models import aion_lite_llm

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

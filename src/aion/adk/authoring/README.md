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

## Model

`aion_lite_llm` builds ADK's `LiteLlm` for Aion's model service: the bearer
token and the principal headers of the turn are resolved on every request.
Its model is a catalog ID, or a function that picks the ID for each request
from the turn's Aion runtime context. A function suits a model the deployment
selects in the environment's configuration, which is known only once a turn
arrives:

```python
from google.adk.agents import LlmAgent
from aion.adk.authoring import aion_lite_llm


def environment_model(runtime):
    environment = runtime.get_environment() if runtime else None
    return environment.get_configuration_variable("model") if environment else None


agent = LlmAgent(name="assistant", model=aion_lite_llm(environment_model))
```

The function runs on the event loop before every request. An `async def`
function is awaited, so it may wait for I/O; a plain one must not block. It
receives `None` outside an Aion server. When it returns no model, the
request is not sent and the task fails with that reason. A model the agent's
`before_model_callback` writes into the request is used as it is.

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
derives Aion MCP URLs, Version bearer auth, and callback attribution at runtime.
Use `capability_references` for explicit SDK-level subject + kind + key
references. Use `runtime_capability_references` when the subject must be
resolved from the current request.

The current request is the turn's Aion runtime context. The toolset finds it
on the invocation behind the `readonly_context` ADK passes, where the server
keeps it, and otherwise takes the context of the turn running in this task;
`context_provider` replaces that lookup for an agent that keeps it elsewhere.

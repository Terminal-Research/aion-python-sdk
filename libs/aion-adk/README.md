# aion-adk

Google ADK authoring toolkit for Aion. Provides helpers that agent authors
import directly when building ADK agents for Aion.

---

## Installation

```bash
pip install aion-adk
```

The Aion Server control-plane plugin lives in `aion-plugin-adk`; this package
is the authoring-facing companion and can be installed alongside it when
serving ADK agents through Aion.

---

## Models

Use `aion_lite_llm` when you want Google ADK model calls to flow through
Aion's OpenAI-compatible model proxy:

```python
from google.adk.agents import Agent
from aion.adk.models import aion_lite_llm

agent = Agent(
    name="research_agent",
    model=aion_lite_llm("model-id-from-control-plane"),
)
```

The helper returns ADK's `LiteLlm` configured with `api_base` pointing at
`<AION_API_HOST>/v1` and an Aion JWT provider backed by the configured
`AION_CLIENT_ID` and `AION_CLIENT_SECRET`. Any other keyword arguments are
passed through to ADK's `LiteLlm`. Look up available model IDs in the Aion
control plane model catalog.

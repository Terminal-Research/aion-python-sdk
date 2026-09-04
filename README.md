# Aion Python SDK

[![PyPI](https://img.shields.io/pypi/v/aionto-sdk)](https://pypi.org/project/aionto-sdk/)
[![Python](https://img.shields.io/pypi/pyversions/aionto-sdk)](https://pypi.org/project/aionto-sdk/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/Terminal-Research/aion-python-sdk/blob/main/LICENSE)

A Python library for serving agents over the Agent-to-Agent (A2A) protocol and
running them on the [Aion platform](https://www.aion.to/).

Write an agent with LangGraph or Google ADK, describe it in an `aion.yaml`, and
run `aion serve`. You get an A2A endpoint with an agent card, streaming, and
conversation state — plus a chat client to try it out.

## ✨ Features

- **Framework agnostic**: write agents with LangGraph or Google ADK — the same
  configuration and the same A2A surface either way.
- **A2A protocol compliant**: an agent card, streaming, and conversation state
  without writing protocol code.
- **Configuration driven**: agents are declared in `aion.yaml`; `aion serve`
  runs them.
- **Chat client included**: `aion chat` talks to a running agent from the
  terminal.
- **Local or connected**: runs with no account at all, or add credentials and
  reach the Aion model service and MCP tools.
- **Multi-agent**: a built-in proxy fronts several agents behind one endpoint.

## 🧩 Framework support

| Framework | Authoring toolkit | Server runtime |
|---|---|---|
| LangGraph | ✅ `langgraph-authoring` | ✅ `langgraph-server` |
| Google ADK | ✅ `adk-authoring` | ✅ `adk-server` |

## 🔧 Installation

### Prerequisites

- Python 3.12, 3.13 or 3.14
- Node.js 22+ — only for the bundled `aion chat` client

### Install

| What you get | uv | pip |
|---|---|---|
| Running LangGraph agents | `uv add "aionto-sdk[langgraph-server]"` | `pip install "aionto-sdk[langgraph-server]"` |
| Running Google ADK agents | `uv add "aionto-sdk[adk-server]"` | `pip install "aionto-sdk[adk-server]"` |
| Aion models, tools and messaging in LangGraph agents | `uv add "aionto-sdk[langgraph-authoring]"` | `pip install "aionto-sdk[langgraph-authoring]"` |
| Aion models, tools and messaging in ADK agents | `uv add "aionto-sdk[adk-authoring]"` | `pip install "aionto-sdk[adk-authoring]"` |
| The A2A server and proxy, no agent framework | `uv add "aionto-sdk[server]"` | `pip install "aionto-sdk[server]"` |
| The `aion` CLI and the client libraries only | `uv add aionto-sdk` | `pip install aionto-sdk` |

Each server extra already includes the matching authoring toolkit and the
server itself — install an authoring extra on its own only when something else
runs the agent. Extras combine: `aionto-sdk[langgraph-server,adk-server]`
serves both frameworks from one installation. A plain `aionto-sdk` gives you
the `aion` command and the client libraries; `aion serve` asks for `[server]`
when it is missing, by name.

## 🚀 Quickstart

This example uses LangGraph. For Google ADK, follow the
[ADK quickstart](https://docs.aion.to/sdk/google-adk/quickstart).

Write an agent in `agent.py`:

```python
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, AnyMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages


class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


def reply(state: State) -> dict:
    inbound = state["messages"][-1]
    return {"messages": [AIMessage(content=f"Echo: {inbound.content}")]}


def build_graph():
    graph = StateGraph(State)
    graph.add_node("reply", reply)
    graph.add_edge(START, "reply")
    graph.add_edge("reply", END)
    return graph
```

Point Aion at it in `aion.yaml`:

```yaml
aion:
  agents:
    support:
      path: "./agent.py:build_graph"
      name: "Support Agent"
```

Serve it:

```bash
aion serve
```

And talk to it from another terminal:

```bash
aion chat
```

That is a complete local run — no account and no credentials required. To
connect the agent to the platform, add `AION_CLIENT_ID` and
`AION_CLIENT_SECRET` to a `.env` beside the configuration; without them the
agents simply serve locally.

## 📚 Documentation

Full documentation lives at [docs.aion.to](https://docs.aion.to/sdk/python):

- [Introduction](https://docs.aion.to/sdk/python) — what the SDK runs and how
- [Installation](https://docs.aion.to/sdk/python/installation)
- [LangGraph quickstart](https://docs.aion.to/sdk/langgraph/quickstart) ·
  [Google ADK quickstart](https://docs.aion.to/sdk/google-adk/quickstart)
- [`aion.yaml` reference](https://docs.aion.to/sdk/python/configuration/aion-yaml) ·
  [Environment variables](https://docs.aion.to/sdk/python/configuration/environment-variables)
- [CLI reference](https://docs.aion.to/sdk/python/running/cli) ·
  [Multiple agents and the proxy](https://docs.aion.to/sdk/python/running/multi-agent-proxy)
- [Troubleshooting](https://docs.aion.to/sdk/python/troubleshooting)

In the repository, covering what the documentation site does not:

- [Development Guide](https://github.com/Terminal-Research/aion-python-sdk/blob/main/docs/development/README.md)
  — working on the SDK itself
- [Publishing to PyPI](https://github.com/Terminal-Research/aion-python-sdk/blob/main/docs/publishing-pypi.md)
  — how the package is built and released

## 🤝 Contributing

Contributions are welcome. For development setup and how to run the test
suites, see the
[Development Guide](https://github.com/Terminal-Research/aion-python-sdk/blob/main/docs/development/README.md).

## 📄 License

This project is licensed under the MIT License. See the
[LICENSE](https://github.com/Terminal-Research/aion-python-sdk/blob/main/LICENSE)
file for more details.

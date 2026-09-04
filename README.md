# Aion Python SDK

[![PyPI](https://img.shields.io/pypi/v/aionto-sdk)](https://pypi.org/project/aionto-sdk/)
[![Python](https://img.shields.io/pypi/pyversions/aionto-sdk)](https://pypi.org/project/aionto-sdk/)
[![Docs](https://img.shields.io/badge/docs-docs.aion.to-blue.svg)](https://docs.aion.to/sdk/python)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/Terminal-Research/aion-python-sdk/blob/main/LICENSE)

A Python library that serves agents over the Agent-to-Agent (A2A) protocol and
runs them on the [Aion platform](https://www.aion.to/). Bring a LangGraph or
Google ADK agent, point an `aion.yaml` at it, and run `aion serve` — you get an
A2A endpoint with an agent card, streaming and conversation state, plus a
terminal client to try it out.

## ✨ Features

- **Framework agnostic**: LangGraph and Google ADK agents get the same
  configuration and the same A2A surface.
- **A2A protocol compliant**: agent card, streaming and conversation state
  without writing protocol code.
- **Four lines of configuration**: an agent needs one field — where to find it.
  Everything else has a default.
- **No account required**: runs fully locally; add credentials to reach the
  Aion model service and MCP tools.
- **Multi-agent**: a built-in proxy fronts several agents behind one endpoint,
  and `aion chat` talks to any of them from the terminal.

## 🚀 Getting started

### Prerequisites

- Python 3.12, 3.13 or 3.14
- Node.js 22+ — only for the bundled `aion chat` client

### 🔧 Installation

Install the SDK plus the extra for the framework you use.

| Feature | `uv` | `pip` |
|---|---|---|
| **Core SDK** — the `aion` CLI and client libraries | `uv add aionto-sdk` | `pip install aionto-sdk` |
| **LangGraph agents** | `uv add "aionto-sdk[langgraph-server]"` | `pip install "aionto-sdk[langgraph-server]"` |
| **Google ADK agents** | `uv add "aionto-sdk[adk-server]"` | `pip install "aionto-sdk[adk-server]"` |
| **LangGraph authoring** | `uv add "aionto-sdk[langgraph-authoring]"` | `pip install "aionto-sdk[langgraph-authoring]"` |
| **ADK authoring** | `uv add "aionto-sdk[adk-authoring]"` | `pip install "aionto-sdk[adk-authoring]"` |

The `*-authoring` extras are the toolkit on its own — Aion models, tools and
messaging inside your agent — for agents that something other than `aion serve`
runs.

## 💡 Example

A LangGraph agent, served over A2A, with four lines of configuration.

1. **Write the agent.** A plain LangGraph graph, returned uncompiled: Aion
   compiles it and attaches the checkpointer, so conversation state comes for
   free.

   ```python
   # agent.py
   from typing import Annotated, TypedDict

   from langchain_core.messages import AIMessage, AnyMessage
   from langgraph.graph import END, START, StateGraph
   from langgraph.graph.message import add_messages


   class State(TypedDict):
       messages: Annotated[list[AnyMessage], add_messages]


   def reply(state: State) -> dict:
       question = state["messages"][-1].content
       return {"messages": [AIMessage(content=f"Echo: {question}")]}


   def create_graph() -> StateGraph:
       workflow = StateGraph(State)
       workflow.add_node("reply", reply)
       workflow.add_edge(START, "reply")
       workflow.add_edge("reply", END)
       return workflow
   ```

2. **Point Aion at it.** `path` is the only field an agent needs.

   ```yaml
   # aion.yaml
   aion:
     agents:
       support:
         path: "./agent.py:create_graph"
   ```

3. **Serve it.** The port is assigned automatically and printed on startup.

   ```bash
   aion serve
   ```

4. **Talk to it** from another terminal.

   ```bash
   aion chat
   ```

That is a complete local run — no account, no credentials. To connect the agent
to the platform, add `AION_CLIENT_ID` and `AION_CLIENT_SECRET` to a `.env`
beside the configuration.

For Google ADK the configuration is the same — see the
[ADK quickstart](https://docs.aion.to/sdk/google-adk/quickstart).

## 📚 Documentation

Full documentation lives at [docs.aion.to](https://docs.aion.to/sdk/python):

- [LangGraph quickstart](https://docs.aion.to/sdk/langgraph/quickstart) ·
  [Google ADK quickstart](https://docs.aion.to/sdk/google-adk/quickstart)
- [`aion.yaml` reference](https://docs.aion.to/sdk/python/configuration/aion-yaml) ·
  [Environment variables](https://docs.aion.to/sdk/python/configuration/environment-variables)
- [CLI reference](https://docs.aion.to/sdk/python/running/cli) ·
  [Multiple agents and the proxy](https://docs.aion.to/sdk/python/running/multi-agent-proxy)
- [Troubleshooting](https://docs.aion.to/sdk/python/troubleshooting)

## 🤝 Contributing

Contributions are welcome. For development setup and how to run the test
suites, see the
[Development Guide](https://github.com/Terminal-Research/aion-python-sdk/blob/main/docs/development/README.md).

## 📄 License

This project is licensed under the MIT License. See the
[LICENSE](https://github.com/Terminal-Research/aion-python-sdk/blob/main/LICENSE)
file for more details.

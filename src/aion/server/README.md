# aion.server

Implementation of an A2A protocol server that wraps an agent written with a
supported framework.

Installed with one of the agent server extras — `pip install
"aionto-sdk[langgraph-server]"` or `pip install "aionto-sdk[adk-server]"`,
depending on the framework the agents are written with. The subpackage itself
ships in every installation; the extra is what brings the `a2a-sdk` server
stack, Starlette and FastAPI it is built on.

This subpackage exposes an `AppFactory` that assembles the agent application on
top of the Google `a2a-sdk` and Starlette.

Graphs are registered based on an `aion.yaml` file located in your project
root. For detailed configuration options and examples, see the [Aion YAML
Configuration Guide](https://docs.aion.to/sdk/python/configuration/aion-yaml).

Custom HTTP endpoints are added through `AppRegistry`: the agent module
registers its own FastAPI routers before the server starts, and the factory
mounts them on the application it builds. `aion.yaml` declares agents and the
MCP proxy, and rejects any other key - see the [AppRegistry
guide](https://docs.aion.to/sdk/python/extensibility/app-registry).

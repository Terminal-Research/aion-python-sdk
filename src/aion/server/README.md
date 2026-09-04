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
root. For detailed configuration options and examples, see the [Aion YAML Configuration Guide](../../../docs/aion-yaml-config.md).

HTTP applications can also be mounted dynamically by listing them under the
`http` section in `aion.yaml`.

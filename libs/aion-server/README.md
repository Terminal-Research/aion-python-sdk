# AION Agent API (A2A)

Implementation of an A2A protocol server that wraps a LangGraph project.

This package exposes a small `A2AServer` utility built on top of the
Google `a2a-sdk` and Starlette.

Graphs are registered based on an `aion.yaml` file located in your project
root. For detailed configuration options and examples, see the [Aion YAML Configuration Guide](../../docs/aion-yaml-config.md).

HTTP applications can also be mounted dynamically by listing them under the
`http` section in `aion.yaml`.
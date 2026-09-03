# aion-server

Framework-neutral A2A protocol server for Aion. Wraps agents written in any
supported framework (LangGraph, Google ADK, or others added via plugins) and
serves them over the A2A protocol.

This package exposes a small `A2AServer` utility built on top of the
Google `a2a-sdk` and Starlette. Framework-specific adapters live in separate
plugin packages (`aion-server-langgraph`, `aion-server-adk`) and are
discovered automatically at startup.

Agents are registered based on an `aion.yaml` file located in your project
root. For detailed configuration options and examples, see the [Aion YAML Configuration Guide](../../docs/aion-yaml-config.md).

HTTP applications can also be mounted dynamically by listing them under the
`http` section in `aion.yaml`.
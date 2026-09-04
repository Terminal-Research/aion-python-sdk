# aion-server-adk

Aion server-side Google ADK integration — implements `AgentPluginProtocol` for the Aion Server runtime.

## Overview

This package provides the server-side plugin for running Google ADK agents within the Aion platform. It handles:

- **Plugin & Adapter** — `ADKPlugin` / `ADKAdapter` implementing `AgentPluginProtocol` / `AgentAdapter`
- **Execution** — `ADKExecutor` / `ADKStreamExecutor` for streaming ADK agent runs
- **Session management** — Memory and PostgreSQL backends via `SessionServiceFactory`
- **Artifact storage** — Memory and A2A-backed artifact service via `ArtifactServiceFactory`
- **State conversion** — `StateConverter` mapping ADK session state to `ExecutionSnapshot`
- **Transformers** — Bidirectional A2A ↔ ADK format conversion

## Namespace

`aion.adk.server`

## Installation

```toml
aion-server-adk = { git = "https://github.com/Terminal-Research/aion-python-sdk", branch = "main", subdirectory = "libs/aion-server-adk" }
```

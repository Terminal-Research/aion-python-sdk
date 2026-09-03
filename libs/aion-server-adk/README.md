# aionto-adk-server

Aion server-side Google ADK integration — implements `AgentPluginProtocol` for
the Aion Server runtime.
The ADK authoring helpers that agent authors import live in `aionto-adk-authoring`.

## Installation

```bash
pip install "aionto-sdk[adk-server]"
```

## Overview

This package provides the server-side plugin for running Google ADK agents
within Aion. It handles:

- **Plugin & Adapter** — `ADKPlugin` / `ADKAdapter` implementing `AgentPluginProtocol` / `AgentAdapter`
- **Execution** — `ADKExecutor` / `ADKStreamExecutor` for streaming ADK agent runs
- **Session management** — Memory and PostgreSQL backends via `SessionServiceFactory`
- **Artifact storage** — Memory and A2A-backed artifact service via `ArtifactServiceFactory`
- **State conversion** — `StateConverter` mapping ADK session state to `ExecutionSnapshot`
- **Transformers** — Bidirectional A2A ↔ ADK format conversion

## Namespace

`aion.adk.server`

## Development

```bash
cd libs/aion-server-adk
poetry install
poetry run pytest
```

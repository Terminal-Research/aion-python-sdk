# aion.adk.server

Aion server-side Google ADK integration — implements `AgentPluginProtocol` for the Aion Server runtime.

## Overview

This subpackage provides the server-side plugin for running Google ADK agents within the Aion platform. It handles:

- **Plugin & Adapter** — `ADKPlugin` / `ADKAdapter` implementing `AgentPluginProtocol` / `AgentAdapter`
- **Execution** — `ADKExecutor` / `ADKStreamExecutor` for streaming ADK agent runs
- **Session management** — Memory and PostgreSQL backends via `SessionServiceFactory`
- **Artifact storage** — Memory and A2A-backed artifact service via `ArtifactServiceFactory`
- **State conversion** — `StateConverter` mapping ADK session state to `ExecutionSnapshot`
- **Transformers** — Bidirectional A2A ↔ ADK format conversion

## Installation

```bash
pip install "aionto-sdk[adk-server]"
```

The extra brings Google ADK, the authoring toolkit (`aion.adk.authoring`) and the server. `aion.server` finds this plugin on its own; without the extra it is skipped, and the skip names the extra to install.

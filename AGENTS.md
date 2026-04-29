# Repo guidelines

This repository is a monorepo containing multiple projects located primarily under `libs/`.

- Whenever you add or modify a subproject, update this file with a brief description so agents can understand its purpose.

## Projects

- **_cli** – an Aion adaptation of `langgraph-cli` for running a LangGraph production server. Out-of-date, was based on langgraph_cli and langgraph_api. We are now using our own servers.
- **_langgraph_api** – internal version of the LangGraph API server exposing HTTP routes, middleware, and async workers.
- **_langgraph_cli** – internal CLI utilities for building Docker images and launching the LangGraph API server.
- **_langgraph_storage** – in-memory storage backend and queue implementation for local LangGraph operations.
- **_a2a-template-langgraph** – example implementation of an A2A protocol serving a LangGraph agent.
- **_agent-workflow** – example implementation of a langgraph project using langgraph_api as a server
- **aion-cli** – command line interface for the Aion Python SDK exposing the `aion` entry point. Delegates `aion chat2` to the packaged standalone chat UI; chat UI auth and environment commands belong to the npm `aio`/`aion-chat` entrypoints and composer slash commands.
- **aion-chat-ui** – standalone React/Ink terminal chat UI built with TypeScript. Packaged for `aion-cli` as the `aion chat2` experience and published to npm as `@terminal-research/aion`, which installs the `aio` executable with an `aion-chat` alias. Includes slash-command request/response mode controls, environment-scoped local settings, and WorkOS CLI/device login with keyring-backed refresh token storage.
- **aion-server** – Google A2A server running a LangGraph agent. Provides task store, agent/plugin lifecycle, and FastAPI application. DB management is delegated to `aion-db`. Graphs and HTTP apps are configured via `aion.yaml` and can be dynamically mounted onto the server.
- **aion-api-client** – provides a low level GraphQL client and a high level
  `ApiClient` interface for the Aion API.
- **aion-mcp** – creates an ASGI proxy for an MCP server defined in `aion.yaml`.
- **aion-db** – centralized DB management layer (postgres driver, migrations, repositories, models). Exposes the full `aion.db.postgres` namespace: `DbManager`, `DbFactory`, `TaskRecord`, `TaskRecordModel`, `TasksRepository`, Alembic migrations, and utilities (`convert_pg_url`, `verify_connection`, `validate_permissions`). Supports future `aion.db.redis` and similar sub-namespaces. Used by `aion-server` and plugins such as `aion-plugin-adk`.

## Additional guidelines

1. Libraries in packages whose names start with an underscore are provided only for context and are **not** intended to be distributed.
2. Always use idiomatic Python and best practices.
3. Document all code with detailed Python docstrings in Google's style, especially at the class and method level; avoid overly terse summaries.
4. Create thourough unit tests for all code using pytest.

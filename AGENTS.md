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
- **aion-agent-cli** – command line interface for the Aion Python SDK exposing the `aion` entry point.
- **aion-server-langgraph** – example Google A2A server running a LangGraph agent. Includes a Postgres database interface, task store, and Alembic migration helpers. Graphs and HTTP apps are configured via `aion.yaml` and can be dynamically mounted onto the server.
- **aion-api-client** – provides a low level GraphQL client and a high level
  `ApiClient` interface for the Aion API.
- **aion-mcp** – creates an ASGI proxy for an MCP server defined in `aion.yaml`.

## Additional guidelines

1. Libraries in packages whose names start with an underscore are provided only for context and are **not** intended to be distributed.
2. Always use idiomatic Python and best practices.
3. Document all code with detailed Python docstrings in Google's style, especially at the class and method level; avoid overly terse summaries.
4. Create thourough unit tests for all code using pytest.

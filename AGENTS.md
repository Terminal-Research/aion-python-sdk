# Repo guidelines

This repository is a monorepo containing multiple projects located primarily under `libs/`.

- Whenever you add or modify a subproject, update this file with a brief description so agents can understand its purpose.

## Projects

- **cli** – an Aion adaptation of `langgraph-cli` for running a LangGraph production server.
- **_langgraph_api** – internal version of the LangGraph API server exposing HTTP routes, middleware, and async workers.
- **_langgraph_cli** – internal CLI utilities for building Docker images and launching the LangGraph API server.
- **_langgraph_storage** – in-memory storage backend and queue implementation for local LangGraph operations.
- **_a2a-template-langgraph** – placeholder for agent-to-agent templates; contains no implementation.
- **_agent-workflow** – placeholder package reserved for workflow examples.

## Additional guidelines

1. Libraries in packages whose names start with an underscore are provided only for context and are **not** intended to be distributed.
2. Always use idiomatic Python and best practices.
3. Document all code with Python docstrings, especially at the class and method level.

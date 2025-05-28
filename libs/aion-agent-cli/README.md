# Aion Agent CLI

Command-line interface for the Aion Python SDK.

This project provides a minimal CLI for running the Aion Agent API server.

When ``structlog`` is available the CLI uses the same colorful logging style as
``_langgraph_cli`` via ``aion_agent_api.logging``. In more minimal
environments it falls back to the standard library's basic configuration.

## Usage

Include ``aion-agent-cli`` as a dependency in your Poetry project. The
package exposes the ``aion`` command via ``[tool.poetry.scripts]`` so once
installed you can run:

```bash
poetry run aion serve
```

This will invoke ``aion_agent_cli.cli`` which in turn starts the local Agent
API server.

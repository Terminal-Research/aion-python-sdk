# Aion Agent CLI

Command-line interface for the Aion Python SDK.

This project provides a minimal CLI for running the Aion Agent API server.

## Usage

Include ``aion-agent-cli`` as a dependency in your Poetry project. The
package exposes the ``aion`` command via ``[tool.poetry.scripts]`` so once
installed you can run:

```bash
poetry run aion serve
```

This will invoke ``aion.cli.cli`` which in turn starts the local Agent
API server.

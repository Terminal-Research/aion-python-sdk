# Aion Monorepo

This repository contains multiple projects.

- **cli** – the original AION Agent API project providing a CLI for deploying LangGraph workflows.
- **aion-agent-cli** – command line interface for the Aion Python SDK.
- **aion-agent-api** – A2A server wrapper for LangGraph projects.

Each project manages its own dependencies and configuration.

## Running the CLI

Install ``aion-agent-cli`` as a dependency (for example with ``pip install -e
libs/aion-agent-cli``). The package provides the ``aion`` command so you can
launch the development server with:

```bash
poetry run aion serve
```

When working directly in this repository you can also run the CLI from the
subproject for local development:

```bash
cd libs/aion-agent-cli
poetry install
poetry run aion serve
```

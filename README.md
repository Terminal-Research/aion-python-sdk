# AION Agent API

A LangGraph server built on top of langgraph-api for deploying agent workflows.

## Overview

AION Agent API is a standalone framework designed to be imported by other agent projects for deployment. It provides the infrastructure to deploy LangGraph-based agents as web services with an easy-to-use CLI.

## Setup

This project uses Poetry for dependency management:

```bash
# Install dependencies
poetry install

# Install the project in development mode
poetry install --no-dev
```

## Usage

### Command Line Interface

AION Agent API provides a command-line interface for launching the server:

```bash
# Using the CLI
poetry run aion-agent-api serve

# View CLI help
poetry run aion-agent-api --help
poetry run aion-agent-api serve --help

# Start with specific host and port
poetry run aion-agent-api serve --host 0.0.0.0 --port 9000

# Enable hot reloading for development
poetry run aion-agent-api serve --reload
```

### Configuration File

You can use a JSON configuration file to specify server settings and register graphs:

```bash
# Use a specific configuration file
poetry run aion-agent-api serve -c my_config.json
```

See `langgraph.json.example` for a template of the configuration file format.

### Programmatic Usage

You can also use the AION Agent API programmatically from your own agent projects:

```python
from aion.agent.api.server import register_graph, run_server
from your_agent_module import your_graph

# Register your LangGraph agent
register_graph(
    graph=your_graph,
    graph_id="my_agent",
    display_name="My Agent",
    description="My awesome LangGraph agent"
)

# Run the server
run_server(host="0.0.0.0", port=8000, reload=True)
```

## Dependencies

- Python 3.11+
- LangGraph API
- LangGraph
- Click (CLI framework)
- Uvicorn (ASGI server)
- Pydantic
- Python-dotenv

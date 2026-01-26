# Multiple Agents Configuration

This guide explains how to configure and run multiple agents with a proxy server.

The Aion SDK supports multiple agent framework integrations including LangGraph and ADK.

## Overview

If your server hosts multiple agents at the same time, you can explicitly select which one to interact with using the `--agent-id` option. The system automatically starts a proxy server that acts as a single entry point for all agents.

Agents can be built using supported frameworks (LangGraph or ADK) and can run simultaneously on the same server.

## Configuration

Create an `aion.yaml` file with multiple agents:

```yaml
aion:
  agents:
    support:
      path: "./support.py:support_agent"

    sales_graph:
      path: "./support.py:sales_agent"
```

## Proxy Server

The system automatically starts a proxy server that acts as a single entry point for all agents:

- The proxy server port is assigned automatically
- Each agent gets its own automatically assigned port
- Agents are accessible via URL path routing: `http://proxy-host/agents/{agent-id}/{path}`
- The proxy automatically routes requests to the appropriate agent based on the URL path

## URL Routing

Each agent can be accessed through the proxy using path-based routing:

- **Support agent**: `http://proxy-host/agents/support/...`
- **Sales agent**: `http://proxy-host/agents/sales_graph/...`

### Examples

- `http://proxy-host/agents/support/.well-known/agent-card.json` - Routes to the Support Agent's Card
- `http://proxy-host/agents/sales_graph/` - Routes to the sales_graph agent (for RPC requests)
- `http://proxy-host/.well-known/manifest.json` - Returns proxy manifest with all available agents

## Interacting with Multiple Agents

To chat with agents through the proxy:

```bash
# Connect to proxy and use default agent
poetry run aion chat

# Connect to proxy and specify agent
poetry run aion chat --agent-id support

# Connect to specific agent server directly
poetry run aion chat --host http://localhost:10001
```

## Port Assignment

When running multiple agents:

```bash
# Start with automatic port assignment (default: ports from 8000-9000)
poetry run aion serve

# Start proxy on specific port (agents will use sequential ports)
poetry run aion serve --port 10000

# Start with custom port range for all services
poetry run aion serve --port-range-start 7000 --port-range-end 8000
```

See the **[CLI Reference](../libs/aion-cli/README.md)** for all available options.

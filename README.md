# Aion Agent SDK

A Python SDK for integrating LangGraph agents with the Agent-to-Agent (A2A) protocol.

## Installation

Install the SDK as a package:

```bash
pip install aion-agent-sdk
```

## Environment Configuration

Create a `.env` file in your project root with the following configuration:

```bash
# Database Configuration
# If not set, memory storage will be used (created automatically with agent startup)
POSTGRES_URL=postgresql://your_username:your_password@localhost:5432/your_database_name

# Application Settings
LOG_LEVEL=INFO
AION_DOCS_URL=https://docs.aion.to/
DISTRIBUTION_ID=your_distribution_id
VERSION_ID=your_version_id
LOGSTASH_HOST=0.0.0.0
LOGSTASH_PORT=5000

# AION API Client
AION_CLIENT_ID=your_client_id_here
AION_CLIENT_SECRET=your_client_secret_here
AION_API_HOST=https://api.aion.to
AION_API_KEEP_ALIVE=60

# CLI Authentication (optional)
AION_CLI_BEARER_TOKEN=your_bearer_token_here
```

### Environment Variables Explained

#### Database Configuration
* **`POSTGRES_URL`** - PostgreSQL connection string. If not provided, the system automatically creates and uses in-memory storage when the agent starts

#### Application Settings
* **`LOG_LEVEL`** - Controls logging verbosity: DEBUG, INFO, WARNING, ERROR (default: INFO)
* **`AION_DOCS_URL`** - URL to the Aion API documentation (default: `https://docs.aion.to/`)
* **`DISTRIBUTION_ID`** - Distribution ID used to identify deployment in Aion platform (optional)
* **`VERSION_ID`** - Version ID used to identify deployment in Aion platform (optional)
* **`LOGSTASH_HOST`** - Logstash server host for centralized logging (optional)
* **`LOGSTASH_PORT`** - Logstash server port for centralized logging (optional)

#### AION API Client
* **`AION_CLIENT_ID`** & **`AION_CLIENT_SECRET`** - Authentication credentials for AION API
* **`AION_API_HOST`** - API host URL (default: `https://api.aion.to`)
* **`AION_API_KEEP_ALIVE`** - Keep alive interval in seconds for API connections (default: 60)

#### CLI Authentication
* **`AION_CLI_BEARER_TOKEN`** - Bearer token for authentication (chat command) - *optional*

## Basic Configuration

Create a minimal `aion.yaml` configuration file:

```yaml
aion:
  agents:
    your_agent_id:
      path: "./agent.py:chat_agent"
```

Ports are assigned automatically for all agents and the proxy server, or can be specified via CLI options.

For extended configuration options including skills and capabilities, see the **[Complete Configuration Guide](docs/aion-yaml-config.md)**.

## Running the Agent

Start the A2A server with proxy and agents:

```bash
# Start with automatic port assignment (default: ports from 8000-9000)
poetry run aion serve

# Start proxy on specific port (agents will use sequential ports)
poetry run aion serve --port 10000

# Start with custom port range for all services
poetry run aion serve --port-range-start 7000 --port-range-end 8000
```

**Available Options:**
* `--port` - Specify proxy server port (agents will use ports starting from proxy_port + 1)
* `--port-range-start` - Starting port for automatic assignment
* `--port-range-end` - Ending port for automatic assignment

The server will automatically:

* Load your `aion.yaml` configuration
* Load environment variables from `.env` file
* Assign available ports to proxy server and agents (or use specified ports)
* Make your agents available via the A2A protocol

## Testing

Test your agents with interactive chat in a separate terminal:

```bash
poetry run aion chat
```

This provides a convenient way to test your agents locally before deployment.

For all available CLI commands and options, see the **[CLI Reference](libs/aion-cli/README.md)**.

## Multiple Agents Configuration

If your server hosts multiple LangGraph agents (graphs) at the same time, you can explicitly select which one to interact with using the `--agent_id` option.

Example `aion.yaml` with multiple agents:

```yaml
aion:
  agents:
    support:
      path: "./support.py:support_agent"

    sales_graph:
      path: "./support.py:sales_agent"
```

### Proxy Server

The system automatically starts a proxy server that acts as a single entry point for all agents:

- The proxy server port is assigned automatically
- Each agent gets its own automatically assigned port
- Agents are accessible via URL path routing: `http://proxy-host/agents/{agent-id}/{path}`
- The proxy automatically routes requests to the appropriate agent based on the URL path

### URL Routing

Each agent can be accessed through the proxy using path-based routing:

- **Support agent**: `http://proxy-host/agents/support/...`
- **Sales agent**: `http://proxy-host/agents/sales_graph/...`

For example:
- `http://proxy-host/agents/support/.well-known/agent-card.json` - Routes to the Support Agent's Card
- `http://proxy-host/agents/sales_graph/` - Routes to the sales_graph agent (for RPC requests)
- `http://proxy-host/.well-known/manifest.json` - Returns proxy manifest with all available agents

### Interacting with Multiple Agents

To chat with agents through the proxy:

```bash
# Connect to proxy and use default agent
poetry run aion chat

# Connect to proxy and specify agent
poetry run aion chat --agent_id support
```

---

## Integration

The SDK provides seamless integration of your LangGraph agents through:

* **Automatic Configuration Loading** - `aion serve` reads your `aion.yaml` setup
* **Environment Management** - Automatic `.env` file loading
* **Protocol Wrapping** - A2A protocol server wrapping your agents
* **Storage Flexibility** - Automatic fallback to memory storage when database not configured
* **Automatic Port Assignment** - Ports for proxy and agents are assigned automatically
* **Proxy Support** - Single entry point for multiple agents with automatic routing

## Documentation

* **[Complete Configuration Guide](docs/aion-yaml-config.md)** - Full YAML options, skills, capabilities
* **[HTTP Endpoints](docs/http_endpoints.md)** - Agent and Proxy Server HTTP endpoints reference
* **[A2A Protocol Extensions](docs/a2a_extensions/main.md)** - Streaming, context management, JSON-RPC methods
* **[API Client](libs/aion-api-client/README.md)** - GraphQL client for integration
* **[CLI Reference](libs/aion-cli/README.md)** - Command-line interface and all available commands

## Quick Start Summary

1. **Install**: `pip install aion-agent-sdk`
2. **Configure**: Create `.env` and `aion.yaml` files
3. **Run**: `poetry run aion serve` (optionally with `--port` or `--port-range-*` options)
4. **Test**: `poetry run aion chat` (in another terminal, optionally with `--agent_id`)

---

## Contributing

For development setup and working with local dependencies, see **[Local Development Setup](docs/local-setup.md)**.

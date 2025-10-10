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
NODE_NAME=your_node_name
DISTRIBUTION_ID=your_distribution_id
VERSION_ID=your_version_id
LOGSTASH_ENDPOINT=logstash-host:port

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
* **`NODE_NAME`** - Node name used to identify deployment in Aion platform (optional)
* **`DISTRIBUTION_ID`** - Distribution ID used to identify deployment in Aion platform (optional)
* **`VERSION_ID`** - Version ID used to identify deployment in Aion platform (optional)
* **`LOGSTASH_ENDPOINT`** - Logstash endpoint for logging (optional)

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
      port: 10000
```

For extended configuration options including skills and capabilities, see the **[Complete Configuration Guide](docs/aion-yaml-config.md)**.

## Running the Agent

Start the A2A server (runs on [http://localhost:10000](http://localhost:10000)):

```bash
poetry run aion serve
```

The server will automatically:

* Load your `aion.yaml` configuration
* Load environment variables from `.env` file
* Make your agents available via the A2A protocol

## Testing

Test your agents with interactive chat in a separate terminal:

```bash
poetry run aion chat
```

This provides a convenient way to test your agents locally before deployment.

For all available CLI commands and options, see the **[CLI Reference](libs/aion-cli/README.md)**.

## Multiple Agents Configuration

### Direct Agent Access

If your server hosts multiple LangGraph agents (graphs) at the same time, you can explicitly select which one to interact with using the `--graph-id` option.

Example `aion.yaml` with multiple agents running on different ports:

```yaml
aion:
  agents:
    support: 
      path: "./support.py:support_agent"
      port: 10001
      
    sales_graph:
      path: "./support.py:sales_agent"
      port: 10002
```

Then you can run:

```bash
poetry run aion chat --graph-id sales_graph
```

to connect specifically to the **sales_graph** agent.

### Proxy Configuration

For more complex deployments with multiple agents, you can use a proxy server to route requests to different agents. This allows all agents to be accessible through a single entry point:

```yaml
aion:
  proxy:
    port: 10000
    
  agents:
    support: 
      path: "./support.py:support_agent"
      port: 10001
      
    sales_graph:
      path: "./support.py:sales_agent"
      port: 10002
```

With proxy configuration:
- The proxy server runs on port `10000` and acts as a single entry point
- Each agent runs on its individual port (`10001`, `10002`, etc.)
- Agents are accessible via URL path routing: `http://localhost:10000/{agent-id}/path`
- The proxy automatically routes requests to the appropriate agent based on the URL path

#### URL Routing

Each agent can be accessed through the proxy using path-based routing:

- **Support agent**: `http://localhost:10000/support/...`
- **Sales agent**: `http://localhost:10000/sales_graph/...`

For example:
- `http://localhost:10000/support/.well-known/agent-card.json` - Routes to the Support Agent's Card
- `http://localhost:10000/sales_graph/` - Routes to the sales_graph agent (for rpc requests)

To start the proxy server with multiple agents:

```bash
poetry run aion serve
```

This will start both the proxy server on port `10000` and all configured agents on their respective ports.

To chat with agents through the proxy:

```bash
# Connect to proxy and use default agent
poetry run aion chat

# Connect to proxy and specify agent
poetry run aion chat --graph-id support
```

---

## Integration

The SDK provides seamless integration of your LangGraph agents through:

* **Automatic Configuration Loading** - `aion serve` reads your `aion.yaml` setup
* **Environment Management** - Automatic `.env` file loading
* **Protocol Wrapping** - A2A protocol server wrapping your agents
* **Storage Flexibility** - Automatic fallback to memory storage when database not configured
* **Proxy Support** - Single entry point for multiple agents with automatic routing

## Documentation

* **[Complete Configuration Guide](docs/aion-yaml-config.md)** - Full YAML options, skills, capabilities
* **[A2A Protocol Extensions](docs/a2a_extensions/main.md)** - Streaming, context management, JSON-RPC methods
* **[API Client](libs/aion-api-client/README.md)** - GraphQL client for integration
* **[CLI Reference](libs/aion-cli/README.md)** - Command-line interface and all available commands

## Quick Start Summary

1. **Install**: `pip install aion-agent-sdk`
2. **Configure**: Create `.env` and `aion.yaml` files
3. **Run**: `poetry run aion serve`
4. **Test**: `poetry run aion chat` (in another terminal, optionally with `--graph-id`)

---

## Contributing

For development setup and working with local dependencies, see **[Local Development Setup](docs/local-setup.md)**.

# Aion Agent SDK

A Python SDK for integrating LangGraph agents with the Agent-to-Agent (A2A) protocol.

## Installation

Install the SDK as a package:

```bash
pip install aion-cli
```

## Environment Configuration

Create a `.env` file in your project root with required credentials:

```bash
AION_CLIENT_ID=your_client_id
AION_CLIENT_SECRET=your_client_secret
```

For all available environment variables and optional configuration, see the **[Environment Variables Guide](docs/environment-variables.md)**.

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

For running multiple agents with a proxy server, see the **[Multiple Agents Guide](docs/multiple-agents.md)**.

## Documentation

* **[Environment Variables Guide](docs/environment-variables.md)** - All configuration variables
* **[Complete Configuration Guide](docs/aion-yaml-config.md)** - Full YAML options, skills, capabilities
* **[Multiple Agents Guide](docs/multiple-agents.md)** - Running multiple agents with proxy
* **[HTTP Endpoints](docs/http_endpoints.md)** - Agent and Proxy Server HTTP endpoints reference
* **[A2A Protocol Extensions](docs/a2a_extensions/main.md)** - Streaming, context management, JSON-RPC methods
* **[API Client](libs/aion-api-client/README.md)** - GraphQL client for integration
* **[CLI Reference](libs/aion-cli/README.md)** - Command-line interface and all available commands

---

## Contributing

For development setup, working with local dependencies, and testing feature branches, see:
- **[Local Development Setup](docs/local-setup.md)** - Full guide for working with monorepo dependencies
- **[Dependency Management Scripts](scripts/deps/README.md)** - Detailed documentation for all dependency scripts

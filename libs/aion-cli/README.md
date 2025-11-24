# Aion Agent CLI

Command-line interface for the Aion Python SDK.

This project provides a minimal CLI for running the Aion Agent API server and interactive chat interface.

## Installation

Include `aion-cli` as a dependency in your Poetry project.

## Commands

### `aion serve`

Starts all configured AION agents and a proxy server that wraps your LangGraph agents with the A2A (Agent-to-Agent) protocol.

**Usage:**

```bash
poetry run aion serve [OPTIONS]
```

**Description:**
This command reads your `aion.yaml` configuration and launches all configured AION Agent API servers. The system automatically runs multiple agents simultaneously and includes a proxy server for unified access. Ports are assigned automatically unless explicitly specified. Each agent server provides HTTP endpoints for interacting with your configured agents and includes automatic API documentation.

**Options:**

* `--port INTEGER` - Port for the proxy server (if not specified, will auto-find starting from 8000)
* `--port-range-start INTEGER` - Starting port of the range for proxy and agents (default: proxy_port + 1 if proxy specified, else 8000)
* `--port-range-end INTEGER` - Ending port of the range for proxy and agents (default: port_range_start + 1000)

**Configuration Requirements:**
- At least one agent must be configured in your `aion.yaml` file
- Proxy server is started automatically

**Examples:**

```bash
# Start with automatic port assignment (default)
poetry run aion serve

# Start proxy on specific port, agents will use ports starting from 10001
poetry run aion serve --port 10000

# Start with custom port range for all services
poetry run aion serve --port-range-start 8000 --port-range-end 9000

# Start proxy on port 7000, agents starting from 7001
poetry run aion serve --port 7000 --port-range-start 7001

# Specify only the port range (proxy will auto-find within the range)
poetry run aion serve --port-range-start 8000 --port-range-end 8100
```

**Welcome Message:**
Upon successful startup, the system displays a welcome message with ASCII art and lists all available endpoints:

```
Welcome to
╔═╗╦╔═╗╔╗╔  ╔═╗╔═╗╔═╗╔╗╔╔╦╗  ╔═╗╔═╗╦
╠═╣║║ ║║║║  ╠═╣║ ╦║╣ ║║║ ║   ╠═╣╠═╝║
╩ ╩╩╚═╝╝╚╝  ╩ ╩╚═╝╚═╝╝╚╝ ╩   ╩ ╩╩  ╩
- Proxy API: http://localhost:{proxy_port}
- Agents:
  * agent-id:
    - Card: http://localhost:{agent_port}/.well-known/agent-card.json
    - Card (Proxy): http://localhost:{proxy_port}/agents/agent-id/.well-known/agent-card.json
    - RPC: http://localhost:{agent_port}
    - RPC (Proxy): http://localhost:{proxy_port}/agents/agent-id/
```



**Server Endpoints:**
The system provides multiple access methods for each agent:

**Proxy Server Access:**
* **Proxy API Base:** `http://localhost:{proxy_port}` (automatically assigned or specified with `--port`)
* **Proxy Manifest:** `http://localhost:{proxy_port}/.well-known/manifest.json`
* **Agent RPC (via Proxy):** `http://localhost:{proxy_port}/agents/{agent_id}/`
* **Agent Card (via Proxy):** `http://localhost:{proxy_port}/agents/{agent_id}/.well-known/agent-card.json`
* **Agent Configuration (via Proxy):** `http://localhost:{proxy_port}/agents/{agent_id}/.well-known/configuration.json`

**Direct Agent Access:**
* **Agent RPC (Direct):** `http://{agent_host}:{agent_port}`
* **Agent Card (Direct):** `http://{agent_host}:{agent_port}/.well-known/agent-card.json`
* **Agent Configuration (Direct):** `http://{agent_host}:{agent_port}/.well-known/configuration.json`

Each configured agent will display both direct and proxy endpoints in the welcome message upon startup.

---

### `aion chat`

Launches an interactive chat interface to test and communicate with your agents in real-time.

**Usage:**

```bash
poetry run aion chat [OPTIONS]
```

**Description:**
Connects to a running A2A server and provides an interactive command-line chat interface. Supports session management, task history, push notifications, and custom extensions.

**Options:**

* `--host TEXT` - Agent URL to connect to (default: `http://localhost:10000`)
* `--bearer-token TEXT` - Bearer token for authentication (can also be set via `AION_CLI_BEARER_TOKEN` environment variable)
* `--session INTEGER` - Session ID to use; `0` creates a random session (default: `0`)
* `--history / --no-history` - Show task history in the chat interface (default: `--no-history`)
* `--push-notifications / --no-push-notifications` - Enable push notifications (default: `--no-push-notifications`)
* `--push-receiver TEXT` - Push notification receiver URL (default: `http://localhost:5000`)
* `--header TEXT` - Custom HTTP headers in format `key=value` (can be used multiple times)
* `--extensions TEXT` - Comma-separated list of extension URIs to enable
* `--graph-id TEXT` - Agent ID to use in proxy (e.g., `hello-world`, `hello-world-chunked`). Be sure, that you passed proxy host in `--host`

**Examples:**

```bash
# Start basic chat session
poetry run aion chat

# Chat with custom agent URL
poetry run aion chat --host http://localhost:8080

# Chat with authentication
poetry run aion chat --bearer-token your-token-here

# Chat with specific session ID and history
poetry run aion chat --session 12345 --history

# Chat with push notifications enabled
poetry run aion chat --push-notifications --push-receiver http://localhost:3000

# Chat with custom headers
poetry run aion chat --header "X-Custom-Header=value" --header "Authorization=Bearer token"

# Chat with extensions enabled
poetry run aion chat --extensions "ext1.example.com,ext2.example.com"

# Chat with explicit agent ID (when using proxy)
poetry run aion chat --graph-id hello-world

# Connect to specific agent server directly
poetry run aion chat --host http://localhost:10001

# Complex example with multiple options
poetry run aion chat \
  --host http://localhost:10000 \
  --bearer-token $TOKEN \
  --session 999 \
  --history \
  --push-notifications \
  --header "X-Client=aion-cli" \
  --graph-id hello-world-chunked
```

**Environment Variables:**

* `AION_CLI_BEARER_TOKEN` - Default bearer token for authentication

**Session Management:**

* Use `--session 0` to create a new random session
* Use `--session <ID>` to continue or create a specific session
* Session history persists across chat sessions when using the same ID

**Push Notifications:**
When enabled, the chat interface can receive real-time notifications from the agent. Ensure your push receiver service is running on the specified URL.

**Graph ID:**
Specifies which agent to interact with when multiple agents are available (works with proxy server).

## Configuration

The CLI reads configuration from your `aion.yaml` file. The configuration must include:

**Required:**
- At least one agent configuration

**Optional:**
- Proxy server configuration for unified agent access

Ensure your project is properly configured before running the server command.

## Multi-Agent Architecture

The AION CLI supports running multiple agents simultaneously:

- Each agent runs in its own process
- Ports are assigned automatically or can be specified via CLI options
- Proxy server provides unified access to all agents
- Failed agents don't prevent other agents from starting
- System monitors all processes and provides graceful shutdown

## Port Assignment Strategy

The system uses intelligent port assignment:

**Default Behavior (no options specified):**
- Proxy port: Auto-find starting from 8000
- Agent ports: Auto-find starting from 8000, within range 8000-9000

**With `--port` specified:**
- Proxy port: Uses specified port
- Agent ports: Auto-find starting from `proxy_port + 1`

**With `--port-range-start` and `--port-range-end`:**
- All ports (proxy and agents) are found within the specified range
- Proxy searches first, then agents use remaining ports

**Examples:**
```bash
# Default: proxy and agents start searching from 8000
aion serve

# Proxy on 10000, agents from 10001+
aion serve --port 10000

# All services between 7000-7100
aion serve --port-range-start 7000 --port-range-end 7100

# Proxy on 5000, agents between 8000-9000
aion serve --port 5000 --port-range-start 8000 --port-range-end 9000
```

## Troubleshooting

**Server won't start:**

* Ensure `aion-server` is installed
* Check that your `aion.yaml` configuration is valid and contains at least one agent
* Verify that ports in the specified range are available
* Try specifying a different port range with `--port-range-start` and `--port-range-end`
* Check logs for specific agent startup failures

**Chat connection issues:**

* Ensure the proxy server is running
* Verify the proxy URL is correct (check the welcome message for the actual port)
* Check authentication token if required
* If using `--agent_id`, ensure the agent ID exists in your configuration

**Port conflicts:**

* If default ports (8000-9000) are busy, specify a custom range
* Use `--port` to set a specific proxy port
* The system will automatically skip occupied ports within the range
* Check system logs to see which ports were assigned

# Aion Agent CLI

Command-line interface for the Aion Python SDK.

This project provides a minimal CLI for running the Aion Agent API server and interactive chat interface.

## Installation

Include `aion-cli` as a dependency in your Poetry project.

## Commands

### `aion serve`

Starts all configured AION agents and an optional proxy server that wraps your LangGraph agents with the A2A (Agent-to-Agent) protocol.

**Usage:**

```bash
poetry run aion serve
```

**Description:**
This command reads your `aion.yaml` configuration and launches all configured AION Agent API servers. The system can run multiple agents simultaneously and includes an optional proxy server for unified access. Each agent server provides HTTP endpoints for interacting with your configured LangGraph agents and includes automatic API documentation.

**Configuration Requirements:**
- At least one agent must be configured in your `aion.yaml` file
- Proxy server configuration is optional

**Example:**

```bash
# Start all configured agents and proxy server
poetry run aion serve
```

**Welcome Message:**
Upon successful startup, the system displays a welcome message with ASCII art and lists all available endpoints:

```
Welcome to
╔═╗╦╔═╗╔╗╔  ╔═╗╔═╗╔═╗╔╗╔╔╦╗  ╔═╗╔═╗╦
╠═╣║║ ║║║║  ╠═╣║ ╦║╣ ║║║ ║   ╠═╣╠═╝║
╩ ╩╩╚═╝╝╚╝  ╩ ╩╚═╝╚═╝╝╚╝ ╩   ╩ ╩╩  ╩
- Proxy API: http://localhost:10000
- Agents:
  * agent-id:
    - Card: http://localhost:port/.well-known/agent-card.json
    - Card (Proxy): http://localhost:10000/agent-id/.well-known/agent-card.json
    - RPC: http://localhost:port
    - RPC (Proxy): http://localhost:10000/agent-id/
```



**Server Endpoints:**
The system provides multiple access methods for each agent:

**Proxy Server Access (if configured):**
* **Proxy API Base:** `http://localhost:10000` (or configured proxy port)
* **Agent RPC (via Proxy):** `http://localhost:10000/{agent_id}/`
* **Agent Card (via Proxy):** `http://localhost:10000/{agent_id}/.well-known/agent-card.json`

**Direct Agent Access:**
* **Agent RPC (Direct):** `http://{agent_host}:{agent_port}`
* **Agent Card (Direct):** `http://{agent_host}:{agent_port}/.well-known/agent-card.json`

Each configured agent will display both direct and proxy endpoints (if proxy is enabled) in the welcome message upon startup.

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
- Agents can be configured with different ports, and settings
- Optional proxy server provides unified access to all agents
- Failed agents don't prevent other agents from starting
- System monitors all processes and provides graceful shutdown

## Troubleshooting

**Server won't start:**

* Ensure `aion-server-langgraph` is installed
* Check that your `aion.yaml` configuration is valid and contains at least one agent
* Verify that configured ports are not already in use
* Check logs for specific agent startup failures

**Chat connection issues:**

* Ensure the agent server (or proxy server) is running
* Verify the agent URL is correct  
* Check authentication token if required
* If using `--graph-id`, ensure the agent ID exists

**Multi-agent issues:**

* Check that each agent is configured with unique ports
* Review startup logs to identify which agents failed to start
* Use proxy server for simplified agent access

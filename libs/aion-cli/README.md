# Aion Agent CLI

Command-line interface for the Aion Python SDK.

This project provides a minimal CLI for running the Aion Agent API server and interactive chat interface.

## Installation

Include `aion-agent-cli` as a dependency in your Poetry project. The package exposes the `aion` command via `[tool.poetry.scripts]` so once installed you can run the commands.

## Commands

### `aion serve`

Starts the local Agent API server that wraps your LangGraph agents with the A2A (Agent-to-Agent) protocol.

**Usage:**

```bash
poetry run aion serve [OPTIONS]
```

**Description:**
This command reads your `aion.yaml` configuration and launches the AION Agent API server. The server provides HTTP endpoints for interacting with your configured LangGraph agents and includes automatic API documentation.

**Options:**

* `--host TEXT` - Server host address (default: `localhost`)
* `--port INTEGER` - Server port number (default: `10000`)

**Example:**

```bash
# Start server on default host and port
poetry run aion serve

# Start server on specific host and port
poetry run aion serve --host 0.0.0.0 --port 8080
```

**Server Endpoints:**

* API Base: `http://{host}:{port}`
* Agent RPC: `http://{host}:{port}/{graph_id}/rpc`
* Agent Card: 
  *  {graph_id}: `http://{host}:{port}/.well-known/{graph_id}/agent-card.json`

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

* `--agent TEXT` - Agent URL to connect to (default: `http://localhost:10000`)
* `--bearer-token TEXT` - Bearer token for authentication (can also be set via `AION_CLI_BEARER_TOKEN` environment variable)
* `--session INTEGER` - Session ID to use; `0` creates a random session (default: `0`)
* `--history / --no-history` - Show task history in the chat interface (default: `--no-history`)
* `--push-notifications / --no-push-notifications` - Enable push notifications (default: `--no-push-notifications`)
* `--push-receiver TEXT` - Push notification receiver URL (default: `http://localhost:5000`)
* `--header TEXT` - Custom HTTP headers in format `key=value` (can be used multiple times)
* `--extensions TEXT` - Comma-separated list of extension URIs to enable
* `--graph-id TEXT` - Graph ID to use (default: `None`)

**Examples:**

```bash
# Start basic chat session
poetry run aion chat

# Chat with custom agent URL
poetry run aion chat --agent http://localhost:8080

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

# Chat with explicit graph ID
poetry run aion chat --graph-id my-agent-graph

# Complex example with multiple options
poetry run aion chat \
  --agent http://prod-agent.example.com \
  --bearer-token $TOKEN \
  --session 999 \
  --history \
  --push-notifications \
  --header "X-Client=aion-cli" \
  --graph-id customer-support
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
The `--graph-id` option allows you to explicitly specify which LangGraph agent graph to interact with, useful when the server hosts multiple graphs under the same A2A API.

## Configuration

The CLI reads configuration from your `aion.yaml` file. Ensure your project is properly configured before running the server command.

## Troubleshooting

**Server won't start:**

* Ensure `aion-server-langgraph` is installed
* Check that your `aion.yaml` configuration is valid
* Verify the specified port is not already in use

**Chat connection issues:**

* Ensure the agent server is running
* Verify the agent URL is correct
* Check authentication token if required

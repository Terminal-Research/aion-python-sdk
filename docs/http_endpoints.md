# AION HTTP Endpoints

This document describes the HTTP endpoints exposed by AION components: **Agents** and **Proxy Server**.

---

## Agent Endpoints

AION agents expose the following HTTP endpoints:

### 1. JSON-RPC Endpoint

**Path:** `/`
**Method:** `POST`
**Purpose:** Main endpoint for Agent-to-Agent (A2A) communication using JSON-RPC 2.0 protocol

Handles all agent-to-agent method calls including:
- Standard A2A methods (`task/send`, `task/cancel`, etc.)
- Custom AION methods (`context/get`, `context/list`, etc.)
- Streaming requests and responses

### 2. Agent Card Endpoint

**Path:** `/.well-known/agent-card.json`
**Method:** `GET`
**Purpose:** Agent metadata and discovery information

Returns agent card with:
- Agent name, description, and version
- Supported capabilities/methods
- Additional metadata

### 3. Configuration Endpoint

**Path:** `/.well-known/configuration.json`
**Method:** `GET`
**Purpose:** Agent configuration and protocol version information

Returns agent configuration with:
- Protocol version
- Agent-specific configuration details

See more: [configuration.json specification](https://www.notion.so/appmail/configuration-json-2a7c8d8a1c1a80368745ebaab40455fb)

### 4. Health Check Endpoint

**Path:** `/health/`
**Method:** `GET`
**Purpose:** Agent health status monitoring

Returns `{"status": "healthy"}` when agent is operational.

---

## Proxy Server Endpoints

The AION Proxy Server provides centralized routing and monitoring:

### 1. Proxy Health Check

**Path:** `/health/`
**Method:** `GET`
**Purpose:** Proxy server health status

Returns `{"status": "healthy"}` when proxy is operational.

### 2. System Health Check

**Path:** `/health/system/`
**Method:** `GET`
**Purpose:** System-wide health monitoring (proxy + all agents)

Returns:
- `proxy_status` - Proxy server status
- `overall_agents_status` - Overall status ("healthy" or "degraded")
- `agents` - Health status for each configured agent with URL, status code, and errors (if any)

Polls each agent's `/health/` endpoint with 5-second timeout.

### 3. Proxy Manifest Endpoint

**Path:** `/.well-known/manifest.json`
**Method:** `GET`
**Purpose:** Proxy server manifest with service information and agent endpoints

Returns manifest with:
- Service name and version
- List of available agents and their endpoints
- Protocol version information

See more: [manifest.json specification](https://www.notion.so/appmail/manifest-json-2a7c8d8a1c1a8082b7a7dcf6b42eec93)

### 4. Agent Request Forwarding

**Path:** `/agents/{agent_id}/{path}`
**Methods:** `GET`, `POST`, `PUT`, `DELETE`, `PATCH`, `OPTIONS`, `HEAD`
**Purpose:** Forward requests to specific agents

Routes requests to agents based on `agent_id` in URL:
- `{agent_id}` - Target agent identifier from configuration
- `{path}` - Path to forward to the agent

Examples:
- `/agents/research-agent/` - Forward JSON-RPC request to research-agent
- `/agents/research-agent/.well-known/agent-card.json` - Get agent card
- `/agents/research-agent/health/` - Check agent health

Error responses:
- `404` - Agent not found
- `503` - Agent unavailable
- `504` - Agent timeout
- `500` - Proxy error

---

## Endpoint Summary

### Agent Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/` | POST | JSON-RPC A2A communication |
| `/.well-known/agent-card.json` | GET | Agent metadata and discovery |
| `/.well-known/configuration.json` | GET | Agent configuration and protocol version |
| `/health/` | GET | Agent health status |

### Proxy Server Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health/` | GET | Proxy health check |
| `/health/system/` | GET | System-wide health (proxy + all agents) |
| `/.well-known/manifest.json` | GET | Proxy manifest with service info and agent endpoints |
| `/agents/{agent_id}/{path}` | ANY | Forward request to agent |

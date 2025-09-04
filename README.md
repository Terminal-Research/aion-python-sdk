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
POSTGRES_URL=postgresql://david_terminal_user:david_terminal_user_password@localhost:5432/david_terminal_agent_db

# Logging
LOG_LEVEL=DEBUG

# AION API Client
AION_CLIENT_ID=28afd668-68ec-46-837d926fb670
AION_CLIENT_SECRET=C6ZIEV4lmjYtbh+iIrCstyxoheMzyc=
AION_API_CLIENT_ENV=development  # or 'production'

# CLI Authentication (optional)
AION_CLI_BEARER_TOKEN=your_bearer_token_here
```

### Environment Variables Explained

- **`POSTGRES_URL`** - PostgreSQL connection string. If not provided, the system automatically creates and uses in-memory storage when the agent starts
- **`LOG_LEVEL`** - Controls logging verbosity (DEBUG, INFO, WARNING, ERROR) - *currently not used*
- **`AION_CLIENT_ID`** & **`AION_CLIENT_SECRET`** - Authentication credentials for AION API
- **`AION_API_CLIENT_ENV`** - Environment setting (`development` or `production`)
- **`AION_CLI_BEARER_TOKEN`** - Bearer token for authentication (chat command) - *optional*

## Basic Configuration

Create a minimal `aion.yaml` configuration file:

```yaml
aion:
  agent:
    my_bot: "./agent.py:chat_agent"
```

For extended configuration options including skills and capabilities, see the **[Complete Configuration Guide](docs/aion-yaml-config.md)**.

## Running the Agent

Start the A2A server (runs on http://localhost:10000):

```bash
poetry run aion serve
```

The server will automatically:
- Load your `aion.yaml` configuration
- Load environment variables from `.env` file
- Make your agents available via the A2A protocol

## Testing

Test your agents with interactive chat in a separate terminal:

```bash
poetry run aion chat
```

This provides a convenient way to test your agents locally before deployment.

For all available CLI commands and options, see the **[CLI Reference](libs/aion-cli/README.md)**.

## Integration

The SDK provides seamless integration of your LangGraph agents through:

- **Automatic Configuration Loading** - `aion serve` reads your `aion.yaml` setup
- **Environment Management** - Automatic `.env` file loading
- **Protocol Wrapping** - A2A protocol server wrapping your agents
- **Storage Flexibility** - Automatic fallback to memory storage when database not configured

## Documentation

- **[Complete Configuration Guide](docs/aion-yaml-config.md)** - Full YAML options, skills, capabilities
- **[A2A Protocol Extensions](docs/a2a_extensions/main.md)** - Streaming, context management, JSON-RPC methods  
- **[API Client](libs/aion-api-client/README.md)** - GraphQL client for integration
- **[CLI Reference](libs/aion-cli/README.md)** - Command-line interface and all available commands

## Quick Start Summary

1. **Install**: `pip install aion-agent-sdk`
2. **Configure**: Create `.env` and `aion.yaml` files
3. **Run**: `poetry run aion serve`
4. **Test**: `poetry run aion chat` (in another terminal)

Your agents are now running and accessible via the A2A protocol at `http://localhost:10000`.

# Aion Agent CLI

Command-line interface for the Aion Python SDK.

This project provides a minimal CLI for running the Aion Agent API server.

When ``structlog`` is available the CLI uses the same colorful logging style as
``_langgraph_cli`` via ``aion_agent_api.logging``. In more minimal
environments it falls back to the standard library's basic configuration.

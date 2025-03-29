"""AION Agent API: A LangGraph Server Framework

This package provides a framework for deploying LangGraph-based agents via an API server.
It includes a CLI for managing the server and utilities for configuring and running it.
"""

# Re-export only essential components for convenient imports
# Avoid importing app directly to prevent premature loading of langgraph_api
from aion.api.agent.server import run_server
from aion.api.agent.cli import cli

__all__ = ["run_server", "cli"]

__version__ = "0.1.0"

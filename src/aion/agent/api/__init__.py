"""AION Agent API: A LangGraph Server Framework

This package provides a framework for deploying LangGraph-based agents via an API server.
It includes a CLI for managing the server and utilities for configuring and running it.
"""

# Re-export key components for convenient imports
from aion.agent.api.server import app, register_graph, run_server
from aion.agent.api.cli import cli

__all__ = ["app", "register_graph", "run_server", "cli"]

__version__ = "0.1.0"

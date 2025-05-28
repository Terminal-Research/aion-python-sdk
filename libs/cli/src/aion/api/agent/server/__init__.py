"""
Server implementation for AION Agent API.

This module provides the core server functionality for deploying LangGraph agents.
"""

# Import runner from runner.py
from aion.api.agent.server.runner import run_server

# Do not import server.py here to avoid executing langgraph_api imports early
# We'll import app only when explicitly requested via run_server

__all__ = ["run_server"]

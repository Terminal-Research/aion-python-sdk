"""Utilities for working with proxy paths and URLs."""

from __future__ import annotations

from aion.proxy.constants import build_agent_path


def format_agent_proxy_path(agent_id: str, path: str = "") -> str:
    """Format a proxy path for an agent served behind the proxy.

    Delegates to the proxy's own builder rather than formatting the URL template
    again here. Spelling the address a second time is how this helper and
    ``build_agent_path`` came to disagree about the trailing slash on an agent's
    root, which put two different addresses for one endpoint into circulation -
    the CLI advertised one, the OpenAPI schema the other.

    Args:
        agent_id: The agent identifier (e.g., 'my-agent', 'langgraph-agent')
        path: The path to append (e.g., '.well-known/agent-card.json', '').
              Leading slashes will be automatically removed.

    Returns:
        Formatted proxy path
        (e.g., '/agents/my-agent/.well-known/agent-card.json')
    """
    return build_agent_path(agent_id, path)

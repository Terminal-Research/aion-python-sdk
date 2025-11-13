"""Utilities for working with proxy paths and URLs."""

from __future__ import annotations

from aion.proxy.constants import AGENT_PROXY_URL


def format_agent_proxy_path(agent_id: str, path: str = "") -> str:
    """Format a proxy path using the AGENT_PROXY_URL template.

    This function constructs a complete proxy path by combining the agent ID
    and a relative path using the standard AGENT_PROXY_URL template pattern.
    It ensures there are no double slashes in the resulting path.

    Args:
        agent_id: The agent identifier (e.g., 'my-agent', 'langgraph-agent')
        path: The path to append (e.g., '.well-known/agent-card.json', '').
              Leading slashes will be automatically removed.

    Returns:
        Formatted proxy path without leading slash
        (e.g., 'agents/my-agent/.well-known/agent-card.json')
    """
    # Remove leading slashes from the path to avoid double slashes
    clean_path = path.lstrip("/")

    return (
        AGENT_PROXY_URL
        .replace("{path:path}", "{path}")
        .format(agent_id=agent_id, path=clean_path)
    )

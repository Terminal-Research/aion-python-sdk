"""URL patterns, path constants, and route builder utilities for the proxy."""

import re

HEALTH_CHECK_URL = "/health/"
SYSTEM_HEALTH_CHECK_URL = "/health/system/"
MANIFEST_URL = "/.well-known/manifest.json"

# The one place the shape of an agent's address is written down. Everything below
# is derived from it, and build_agent_path is the only supported way to produce
# one: an address spelled by hand in a second place drifts from this one silently,
# because a caller that guesses wrong is redirected rather than refused.
AGENT_PROXY_PREFIX = "/agents/{agent_id}"
AGENT_PROXY_URL = f"{AGENT_PROXY_PREFIX}/{{path:path}}"

# Matches both forms an agent's root can be written in, since both are routed.
# Group 2 is None for a bare root - use ``or ''``.
AGENT_PATH_PATTERN = re.compile(r'^/agents/([^/]+)(?:/(.*))?$')


def build_agent_path(agent_id: str, path: str = "") -> str:
    """Build a full agent proxy path from agent_id and path.

    The root form carries no trailing slash. That is what an OpenAPI ``servers``
    entry needs - Swagger UI appends operation paths that already start with a
    slash - and it is a valid address in its own right, routed directly rather
    than through a redirect.

    Args:
        agent_id: The agent identifier
        path: Optional sub-path (e.g., 'docs', 'openapi.json'). Leading slashes
            are stripped so callers can pass either spelling.

    Returns:
        Full agent proxy path (e.g., '/agents/my-agent/docs')
    """
    base = AGENT_PROXY_PREFIX.format(agent_id=agent_id)
    clean_path = path.lstrip("/")
    return f"{base}/{clean_path}" if clean_path else base

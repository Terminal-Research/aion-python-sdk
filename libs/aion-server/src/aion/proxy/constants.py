import re

HEALTH_CHECK_URL = "/health/"
SYSTEM_HEALTH_CHECK_URL = "/health/system/"
MANIFEST_URL = "/.well-known/manifest.json"
AGENT_PROXY_URL = "/agents/{agent_id}/{path:path}"

# Derived constants
AGENT_PROXY_PREFIX = AGENT_PROXY_URL.rsplit('/', 1)[0]  # "/agents/{agent_id}"
AGENT_PATH_PATTERN = re.compile(r'^/agents/([^/]+)/(.*)')


def build_agent_path(agent_id: str, path: str = "") -> str:
    """Build a full agent proxy path from agent_id and path.

    Args:
        agent_id: The agent identifier
        path: Optional sub-path (e.g., 'docs', 'openapi.json')

    Returns:
        Full agent proxy path (e.g., '/agents/my-agent/docs')
    """
    base = AGENT_PROXY_PREFIX.format(agent_id=agent_id)
    return f"{base}/{path}" if path else base

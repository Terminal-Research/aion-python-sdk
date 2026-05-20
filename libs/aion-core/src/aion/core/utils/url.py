from typing import Optional
from urllib.parse import urlparse

__all__ = [
    "parse_host_port",
]


def parse_host_port(endpoint: str) -> Optional[tuple[str, int]]:
    """Extract host and port from endpoint string.

    Supports formats:
    - host:port (e.g., "localhost:8081")
    - scheme://host:port (e.g., "https://localhost:8081")

    Args:
        endpoint: Endpoint string to parse

    Returns:
        Tuple of (host, port) or None if parsing fails
    """
    if not endpoint:
        return None

    endpoint = endpoint.strip()

    # Check if endpoint contains a scheme (http://, https://, etc.)
    if '://' in endpoint:
        parsed = urlparse(endpoint)
        if parsed.hostname and parsed.port:
            return parsed.hostname, parsed.port
        return None

    # Handle simple "host:port" format
    if ':' in endpoint:
        host, port_str = endpoint.rsplit(':', 1)
        return host, int(port_str)

    return None

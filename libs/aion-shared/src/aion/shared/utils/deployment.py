__all__ = [
    "get_service_name",
    "get_api_version",
]


def get_service_name() -> str:
    """Get name of the service."""
    return "aion-agent-server"

def get_api_version() -> str:
    """Get API version."""
    return "aion.manifest/v1"

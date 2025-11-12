from a2a.utils import AGENT_CARD_WELL_KNOWN_PATH

from aion.shared.types import A2AManifest

__all__ = [
    "get_service_name",
    "get_api_version",
    "get_protocol_version",
    "generate_a2a_manifest",
]


def get_service_name() -> str:
    """Get name of the service."""
    return "aion-agent-server"


def get_api_version() -> str:
    """Get API version."""
    return "aion.manifest/v1"


def get_protocol_version() -> str:
    """Get protocol version."""
    return "aion.agent.configuration/v1"


def generate_a2a_manifest(
    agent_ids: list[str],
    endpoint_template: str
) -> A2AManifest:
    """
    Generate an A2A manifest with endpoints for specified agents.

    Creates a manifest with API version, service name, and dynamically constructed
    endpoint URLs for each agent. The endpoint template should contain {agent_id}
    and {path} placeholders.

    Args:
        agent_ids: List of agent identifiers to include in the manifest.
        endpoint_template: URL template with {agent_id} and {path} placeholders.
                          Example: "/{agent_id}/{path}" or "/api/agents/{agent_id}/{path}"

    Returns:
        A2AManifest: Configured manifest with endpoints for all provided agents.
    """
    return A2AManifest(
        api_version=get_api_version(),
        name=get_service_name(),
        endpoints={
            agent_id: endpoint_template.format(
                agent_id=agent_id,
                path=AGENT_CARD_WELL_KNOWN_PATH.lstrip('/')
            )
            for agent_id in agent_ids
        }
    )

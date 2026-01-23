from aion.shared.types import A2AManifest
from aion.shared.utils.deployment import generate_a2a_manifest as generate_a2a_manifest_util

from .constants import AGENT_PROXY_PREFIX

__all__ = [
    "generate_a2a_manifest",
]


def generate_a2a_manifest(agent_ids: list[str]) -> A2AManifest:
    """
    Generate a proxy manifest containing endpoints for specified agent IDs.

    Creates a manifest with API version, service name, and dynamically constructed
    endpoint URLs for each agent, using the proxy-specific URL template.

    Args:
        agent_ids: List of agent identifiers to include in the manifest.

    Returns:
        A2AManifest: Configured manifest with endpoints for all provided agents.
    """
    return generate_a2a_manifest_util(
        agent_ids=agent_ids,
        endpoint_template=AGENT_PROXY_PREFIX
    )
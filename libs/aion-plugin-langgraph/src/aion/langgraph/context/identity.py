from dataclasses import dataclass
from typing import Dict, Optional

from aion.shared.types.a2a.extensions.distribution import DistributionExtensionV1


@dataclass(frozen=True)
class AgentBehavior:
    """Behavior context for the active execution step."""

    key: str
    """Unique identifier for the agent behavior/graph."""
    version_id: str
    """Version identifier of the agent behavior."""


@dataclass(frozen=True)
class AgentEnvironment:
    """Environment context for the active execution step."""

    id: str
    """Unique identifier for the execution environment."""
    name: str
    """Human-readable name of the environment."""
    configuration_variables: Dict[str, str]
    """Environment-scoped configuration variables as key-value pairs."""


@dataclass(frozen=True)
class AgentIdentity:
    """Agent identity derived from the DistributionExtensionV1 envelope."""

    id: str
    """Unique identifier for the agent on the network."""
    display_name: Optional[str]
    """Human-readable display name for the agent."""
    user_name: Optional[str]
    """System username or handle for the agent."""
    network_type: str
    """Type of network/endpoint where the agent operates (e.g., 'slack', 'teams')."""
    behavior: AgentBehavior
    """Agent behavior metadata including key and version."""
    environment: AgentEnvironment
    """Environment metadata including configuration variables."""

    @classmethod
    def from_distribution(cls, dist_ext: DistributionExtensionV1) -> "AgentIdentity":
        agent_record = next(
            i for i in dist_ext.distribution.identities if i.kind == "principal"
        )
        return cls(
            id=agent_record.id,
            display_name=agent_record.display_name,
            user_name=agent_record.user_name,
            network_type=dist_ext.distribution.endpoint_type,
            behavior=AgentBehavior(
                key=dist_ext.behavior.behavior_key,
                version_id=dist_ext.behavior.version_id,
            ),
            environment=AgentEnvironment(
                id=dist_ext.environment.id,
                name=dist_ext.environment.name,
                configuration_variables=dist_ext.environment.configuration_variables,
            ),
        )

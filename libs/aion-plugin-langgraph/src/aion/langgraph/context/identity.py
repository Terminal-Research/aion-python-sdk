from dataclasses import dataclass
from typing import Dict, Optional

from aion.shared.types.a2a.extensions.distribution import DistributionExtensionV1


@dataclass(frozen=True)
class AgentBehavior:
    """Behavior context for the active execution step."""

    key: str
    version_id: str


@dataclass(frozen=True)
class AgentEnvironment:
    """Environment context for the active execution step."""

    id: str
    name: str
    configuration_variables: Dict[str, str]


@dataclass(frozen=True)
class AgentIdentity:
    """Agent identity derived from the DistributionExtensionV1 envelope."""

    id: str
    display_name: Optional[str]
    user_name: Optional[str]
    network_type: str
    behavior: AgentBehavior
    environment: AgentEnvironment

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

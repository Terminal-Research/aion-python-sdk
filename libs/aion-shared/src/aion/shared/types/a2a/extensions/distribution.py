from typing import Annotated, Dict, List, Literal, Optional, Union

from pydantic import Field

from aion.shared.a2a import A2ABaseModel

__all__ = [
    "AgentIdentityRecord",
    "ExternalIdentityRecord",
    "IdentityRecord",
    "DistributionRecord",
    "BehaviorRecord",
    "EnvironmentRecord",
    "DistributionExtensionV1",
]


class AgentIdentityRecord(A2ABaseModel):
    """Agent identity projection within a distribution."""
    kind: Literal["principal"]
    id: str
    network_type: str
    organization_id: str
    represented_user_id: Optional[str] = None
    display_name: Optional[str] = None
    user_name: Optional[str] = None
    avatar_image_url: Optional[str] = None
    url: Optional[str] = None
    agent_type: Optional[str] = None


class ExternalIdentityRecord(A2ABaseModel):
    """External identity projection within a distribution."""
    kind: Literal["service"]
    id: str
    network_type: str
    organization_id: str
    represented_user_id: Optional[str] = None
    display_name: Optional[str] = None
    user_name: Optional[str] = None
    avatar_image_url: Optional[str] = None
    url: Optional[str] = None


IdentityRecord = Annotated[
    Union[AgentIdentityRecord, ExternalIdentityRecord],
    Field(discriminator="kind"),
]


class DistributionRecord(A2ABaseModel):
    """Distribution context in the Aion control plane."""
    id: str
    endpoint_type: str
    url: str
    identities: List[IdentityRecord]


class BehaviorRecord(A2ABaseModel):
    """Behavior context for the active execution step."""
    id: str
    behavior_key: str
    version_id: str


class EnvironmentRecord(A2ABaseModel):
    """Environment context for the active execution step."""
    id: str
    name: str
    deployment_id: str
    configuration_variables: Dict[str, str]
    daemon_agent_identity_id: Optional[str] = None
    system_prompt: Optional[str] = None


class DistributionExtensionV1(A2ABaseModel):
    """
    Aion Distribution extension payload for A2A metadata.

    Spec: https://docs.aion.to/extensions/aion/distribution/1.0.0
    """
    version: Literal["1.0.0"] = "1.0.0"
    sender_id: Optional[str] = None
    distribution: DistributionRecord
    behavior: BehaviorRecord
    environment: EnvironmentRecord

"""A2A extension models for distribution, behavior, and environment context.

Defines payload models for distribution metadata including principal/service identities,
distribution channels, behavior configuration, and runtime environment details.
"""

from typing import Annotated, Dict, List, Literal, Optional, Union

from pydantic import Field

from aion.core.a2a import A2ABaseModel

__all__ = [
    "PrincipalIdentity",
    "ServiceIdentity",
    "Identity",
    "Distribution",
    "Behavior",
    "Environment",
    "DistributionExtensionV1",
]


class PrincipalIdentity(A2ABaseModel):
    """Aion principal identity directly associated with a distribution.

    This identity represents the principal that owns the distribution channel
    when a principal exists. It is distinct from daemon, system, or other Aion
    agent identities that may be referenced elsewhere, such as the environment's
    daemon agent identity id.
    """

    kind: Literal["principal"] = Field(
        description=(
            "Identity type discriminator. principal means this identity is the "
            "Aion principal associated directly with the distribution channel."
        )
    )
    id: str = Field(
        description="Identity id for the principal associated with this distribution."
    )
    network_type: str = Field(
        description="Communication-network namespace for the identity."
    )
    organization_id: str = Field(description="Owning organization id.")
    represented_user_id: Optional[str] = Field(
        default=None,
        description=(
            "End-user id represented by this principal on its network. For Aion "
            "principal identities, this is the user ownership context for the "
            "distribution when one exists."
        ),
    )
    display_name: Optional[str] = Field(
        default=None,
        description="Display name for rendering.",
    )
    user_name: Optional[str] = Field(
        default=None,
        description="Provider-facing username or handle, normalized for display.",
    )
    avatar_image_url: Optional[str] = Field(
        default=None,
        description="Avatar URL for display.",
    )
    url: Optional[str] = Field(
        default=None,
        description="Card or profile URL for the principal identity, if available.",
    )
    agent_type: Optional[str] = Field(
        default=None,
        description=(
            "Agent type for this principal identity, such as Personal, Principal, "
            "Daemon, or System. The distribution identity projection only includes "
            "the principal identity; daemon identities may be referenced separately "
            "by the environment."
        ),
    )


class ServiceIdentity(A2ABaseModel):
    """External network or service identity directly associated with a distribution.

    Service identities represent the identity from the external network that the
    distribution connects to, such as the provider account or communication
    channel identity. A distribution may have a service identity when it bridges
    an external network, and may also have a principal identity when the channel
    belongs to a known Aion principal.
    """

    kind: Literal["service"] = Field(
        description=(
            "Identity type discriminator. service means this identity is the "
            "external network or service identity associated directly with the "
            "distribution channel."
        )
    )
    id: str = Field(
        description="Identity id for the service identity associated with this distribution."
    )
    network_type: str = Field(
        description="Communication-network namespace for the identity."
    )
    organization_id: str = Field(description="Owning organization id.")
    represented_user_id: Optional[str] = Field(
        default=None,
        description="End-user id represented by this identity on its external network.",
    )
    display_name: Optional[str] = Field(
        default=None,
        description="Display name for rendering.",
    )
    user_name: Optional[str] = Field(
        default=None,
        description="Provider-facing username or handle, normalized for display.",
    )
    avatar_image_url: Optional[str] = Field(
        default=None,
        description="Avatar URL for display.",
    )
    url: Optional[str] = Field(
        default=None,
        description="Profile URL for the service identity, if available.",
    )


Identity = Annotated[
    Union[PrincipalIdentity, ServiceIdentity],
    Field(discriminator="kind"),
]


class Distribution(A2ABaseModel):
    """Distribution context for the request.

    A distribution is the communication-channel adapter that delivered this
    invocation. Its identities are attached directly to that distribution, not
    to the active behavior or environment.
    """

    id: str = Field(description="Distribution identifier in the Aion control plane.")
    endpoint_type: str = Field(
        description="Source/target network type, for example Twitter or A2A."
    )
    url: str = Field(
        description="Distribution-facing A2A URL; currently sourced from the principal identity A2A URL."
    )
    identities: List[Identity] = Field(
        description=(
            "Identities associated directly with this distribution. A distribution "
            "may include a service identity for the external network identity it "
            "connects to, a principal identity for the Aion principal that owns "
            "the channel, both, or neither depending on the distribution type and "
            "available ownership context."
        )
    )


class Behavior(A2ABaseModel):
    """Control-plane behavior metadata for the invoked agent implementation.

    The behavior is the static agent implementation metadata selected for this
    runtime invocation. It corresponds to the behavior/graph configuration the
    agent developer declared in ``aion.yaml``.
    """

    id: str = Field(description="Behavior id in the Aion control plane.")
    behavior_key: str = Field(
        description="Behavior key/graph key from the agent's aion.yaml configuration."
    )
    version_id: str = Field(
        description="Behavior version id used for this runtime invocation."
    )


class Environment(A2ABaseModel):
    """Control-plane environment configuration for the invoked agent runtime.

    The environment is the administrator-configured runtime configuration for
    this server invocation. It supplies the values for configuration variables
    declared by the static behavior configuration.
    """

    id: str = Field(description="Environment id in the Aion control plane.")
    name: str = Field(description="Human-readable environment name.")
    deployment_id: str = Field(
        description="Deployment id associated with this environment."
    )
    configuration_variables: Dict[str, str] = Field(
        description=(
            "Environment configuration key/value pairs resolved for this runtime "
            "invocation. These are the administrator-provided values for variables "
            "defined by the agent behavior configuration."
        )
    )
    daemon_agent_identity_id: Optional[str] = Field(
        default=None,
        description="Daemon agent identity id assigned for internal addressing.",
    )
    system_prompt: Optional[str] = Field(
        default=None,
        description="System prompt associated with this environment, when provided.",
    )

    def get_configuration_variable(self, key: str) -> Optional[str]:
        """Return a runtime configuration variable value by key.

        Args:
            key: Configuration variable key to look up.

        Returns:
            The configured value for ``key``, or ``None`` when the environment
            does not include that variable.
        """
        return self.configuration_variables.get(key)


class DistributionExtensionV1(A2ABaseModel):
    """Aion distribution extension payload for A2A metadata.

    Spec: https://docs.aion.to/a2a/extensions/aion/distribution/1.0.0
    """

    version: Literal["1.0.0"] = Field(
        default="1.0.0",
        description="Distribution extension schema version used to parse this payload.",
    )
    sender_id: Optional[str] = Field(
        default=None,
        description="Source-network sender identifier for this request.",
    )
    distribution: Distribution = Field(
        description=(
            "Distribution context for the request, including identities directly "
            "associated with the communication channel that delivered the invocation."
        )
    )
    behavior: Behavior = Field(
        description=(
            "Control-plane behavior metadata for the agent implementation invoked "
            "by this runtime request. This record describes the static agent "
            "configuration defined in the agent's aion.yaml file."
        )
    )
    environment: Environment = Field(
        description=(
            "Control-plane environment configuration for the agent runtime serving "
            "this request."
        )
    )

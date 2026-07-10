"""A2A extension models for daemon-scoped requests.

Defines the payload for the daemon extension: authenticated,
environment-scoped daemon interaction. See:
https://docs.aion.to/a2a/extensions/aion/daemon/1.0.0
"""

from typing import Literal, Optional

from pydantic import Field

from aion.core.a2a import A2ABaseModel

from .distribution import Behavior, Environment

__all__ = [
    "DaemonIdentity",
    "DaemonExtensionPayload",
]


class DaemonIdentity(A2ABaseModel):
    """Identity record carried by the daemon extension payload.

    Reused for both the target daemon identity (`kind: "daemon"`) and the
    requester identity, which may resolve to any Aion identity kind
    (personal, principal, daemon, system, or service).
    """

    kind: Literal["personal", "principal", "daemon", "system", "service"] = Field(
        description="Identity type discriminator."
    )
    id: str = Field(description="Identity record id.")
    network_type: str = Field(
        description="Network/provider namespace for the identity. Use 'Aion' for Aion identities."
    )
    organization_id: str = Field(description="Owning organization id.")
    represented_user_id: Optional[str] = Field(
        default=None,
        description="End-user id represented by this identity when one exists.",
    )
    display_name: Optional[str] = Field(
        default=None,
        description="Display name for rendering.",
    )
    user_name: Optional[str] = Field(
        default=None,
        description="Provider-facing username or Aion handle.",
    )
    avatar_image_url: Optional[str] = Field(
        default=None,
        description="Avatar URL for display.",
    )
    url: Optional[str] = Field(
        default=None,
        description="Profile, card, or service URL when available.",
    )


class DaemonExtensionPayload(A2ABaseModel):
    """Aion daemon extension payload for A2A metadata.

    Spec: https://docs.aion.to/a2a/extensions/aion/daemon/1.0.0#DaemonExtensionPayload
    """

    daemon_identity: DaemonIdentity = Field(
        description="Daemon identity bound to the target environment."
    )
    requester_identity: Optional[DaemonIdentity] = Field(
        default=None,
        description="Authenticated requester when it resolves to an Aion identity record.",
    )
    behavior: Behavior = Field(
        description="Behavior context selected for the target environment."
    )
    environment: Environment = Field(
        description="Environment context selected for the target daemon identity."
    )

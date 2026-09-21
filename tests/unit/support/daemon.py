"""Builder for the daemon extension payload the evolution tests need.

`DaemonExtensionPayload` is what the platform sends an agent about the
environment it runs in - the configuration variables, the daemon identity the
model calls are attributed to. Nothing in the SDK constructs one, so the tests
that consume it were each building an object shaped like the two attributes
they happened to read. A field renamed or made required in the model would
leave every one of them passing.

Building the real model instead means those tests fail when the payload they
claim to receive stops being a payload the platform could send.
"""

from __future__ import annotations

from typing import Optional

from aion.core.a2a.extensions import Behavior, Environment
from aion.core.a2a.extensions.daemon import DaemonExtensionPayload, DaemonIdentity


def daemon_identity(
    identity_id: str = "daemon-identity-1",
    *,
    organization_id: str = "org-1",
) -> DaemonIdentity:
    return DaemonIdentity(
        kind="daemon",
        id=identity_id,
        network_type="Aion",
        organization_id=organization_id,
    )


def daemon_payload(
    *,
    configuration_variables: Optional[dict[str, str]] = None,
    daemon_agent_identity_id: Optional[str] = "daemon-1",
) -> DaemonExtensionPayload:
    """A payload as the platform would send it, with the two knobs tests vary.

    Args:
        configuration_variables: Environment configuration as the control plane
            supplies it - the evolution handler reads the model name out of it.
        daemon_agent_identity_id: Identity model usage is attributed to; None
            stands for an environment that has none assigned.
    """
    return DaemonExtensionPayload(
        daemon_identity=daemon_identity(),
        behavior=Behavior(id="b-1", behavior_key="main", version_id="v-1"),
        environment=Environment(
            id="env-1",
            name="prod",
            project_id="proj-1",
            deployment_id="dep-1",
            configuration_variables=configuration_variables or {},
            daemon_agent_identity_id=daemon_agent_identity_id,
        ),
    )

"""The daemon extension payload, as a platform would put it on a request.

Written by hand in the wire's own spelling, for the same reason
``distribution.py`` is: a scenario carrying this payload is standing in for the
platform, and the platform does not import the SDK to build it. What the agent
reports back is compared against the values named here, so a field that is
dropped, renamed or re-derived on the way in shows up as a mismatch.
"""

from __future__ import annotations

from typing import Any, Optional

__all__ = [
    "DAEMON_EXTENSION_URI",
    "DAEMON_IDENTITY_ID",
    "REQUESTER_IDENTITY_ID",
    "DAEMON_BEHAVIOR_ID",
    "DAEMON_ENVIRONMENT_ID",
    "daemon_metadata",
]

DAEMON_EXTENSION_URI = "https://docs.aion.to/a2a/extensions/aion/daemon/1.0.0"

DAEMON_IDENTITY_ID = "identity-daemon-scenarios"
REQUESTER_IDENTITY_ID = "identity-requester-scenarios"
DAEMON_BEHAVIOR_ID = "behavior-daemon-scenarios"
DAEMON_ENVIRONMENT_ID = "environment-daemon-scenarios"
DAEMON_ORGANIZATION_ID = "organization-scenarios"


def _identity(kind: str, identity_id: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "id": identity_id,
        "networkType": "Aion",
        "organizationId": DAEMON_ORGANIZATION_ID,
    }


def daemon_metadata(
    *,
    daemon_identity_id: str = DAEMON_IDENTITY_ID,
    requester_identity_id: Optional[str] = REQUESTER_IDENTITY_ID,
    behavior_id: str = DAEMON_BEHAVIOR_ID,
    environment_id: str = DAEMON_ENVIRONMENT_ID,
    configuration_variables: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """Request metadata carrying one daemon extension payload.

    Args:
        daemon_identity_id: The daemon the request is addressed to.
        requester_identity_id: Who is asking. ``None`` omits the requester,
            which the payload allows.
        behavior_id: Behavior selected for the target environment.
        environment_id: Environment the daemon identity is bound to.
        configuration_variables: Configuration the environment carries.

    Returns:
        A mapping to pass as ``ScenarioClient.send(metadata=...)``, keyed by
        the extension URI the server reads it under.
    """
    payload: dict[str, Any] = {
        "daemonIdentity": _identity("daemon", daemon_identity_id),
        "behavior": {
            "id": behavior_id,
            "behaviorKey": "main",
            "versionId": "version-1",
        },
        "environment": {
            "id": environment_id,
            "name": "scenarios-daemon",
            "projectId": "project-scenarios",
            "deploymentId": "deployment-scenarios",
            "configurationVariables": configuration_variables or {},
        },
    }
    if requester_identity_id is not None:
        payload["requesterIdentity"] = _identity("principal", requester_identity_id)
    return {DAEMON_EXTENSION_URI: payload}

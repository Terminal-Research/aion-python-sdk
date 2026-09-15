"""The distribution extension payload, as a platform would put it on a request.

Written by hand in the wire's own spelling rather than built from the SDK's
models: a scenario that needs the payload is standing in for the platform,
and the platform does not import the SDK to build it.
"""

from __future__ import annotations

from typing import Any

__all__ = ["DISTRIBUTION_EXTENSION_URI", "ORGANIZATION_ID", "distribution_metadata"]

DISTRIBUTION_EXTENSION_URI = "https://docs.aion.to/a2a/extensions/aion/distribution/1.0.0"
ORGANIZATION_ID = "organization-scenarios"


def _principal(organization_id: str) -> dict[str, Any]:
    return {
        "kind": "principal",
        "id": "principal-scenarios",
        "identityNetwork": "Aion",
        "identityKind": "Principal",
        "organizationId": organization_id,
    }


def _service() -> dict[str, Any]:
    return {
        "kind": "service",
        "id": "service-scenarios",
        "identityNetwork": "Telegram",
        "identityKind": "Bot",
        "organizationId": "organization-other",
    }


def distribution_metadata(
    *,
    organization_id: str = ORGANIZATION_ID,
    principal: bool = True,
    service: bool = False,
) -> dict[str, Any]:
    """Request metadata carrying one distribution extension payload.

    Args:
        organization_id: Organization the principal identity belongs to - the
            one a stored file is owned by.
        principal: Include a principal identity. Without one the request
            names no organization at all.
        service: Include a service identity as well, under another
            organization; it never decides ownership.

    Returns:
        A mapping to pass as ``ScenarioClient.send(metadata=...)``, keyed by
        the extension URI the server reads it under.
    """
    identities: list[dict[str, Any]] = []
    if principal:
        identities.append(_principal(organization_id))
    if service:
        identities.append(_service())
    return {
        DISTRIBUTION_EXTENSION_URI: {
            "distribution": {
                "id": "distribution-scenarios",
                "endpointType": "A2A",
                "url": "https://example.invalid/a2a",
                "identities": identities,
            },
            "behavior": {"id": "behavior-scenarios", "behaviorKey": "main", "versionId": "version-1"},
            "environment": {
                "id": "environment-scenarios",
                "name": "scenarios",
                "projectId": "project-scenarios",
                "deploymentId": "deployment-scenarios",
                "configurationVariables": {},
            },
        }
    }

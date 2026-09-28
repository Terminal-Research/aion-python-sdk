"""The distribution payload a platform request carries, for the request-path tests.

Built from the real model, so the payload these tests send is one the model
accepts: a field renamed or made required changes it along with the model.
``distribution_metadata`` dumps it in the wire's spelling, as it arrives in
``params.metadata``; a test that needs a malformed payload edits that copy.
"""

from __future__ import annotations

from typing import Any, Optional

from aion.core.a2a.extensions import (
    Behavior,
    Distribution,
    DistributionExtensionV1,
    Environment,
)
from aion.core.constants import DISTRIBUTION_EXTENSION_URI_V1


def distribution_extension(
    distribution_id: str = "dist-1",
    *,
    configuration_variables: Optional[dict[str, str]] = None,
) -> DistributionExtensionV1:
    return DistributionExtensionV1(
        distribution=Distribution(
            id=distribution_id,
            endpoint_type="A2A",
            url="https://example.invalid/a2a",
            identities=[],
        ),
        behavior=Behavior(id="b-1", behavior_key="main", version_id="v-1"),
        environment=Environment(
            id="env-1",
            name="prod",
            project_id="proj-1",
            deployment_id="dep-1",
            configuration_variables=configuration_variables or {},
        ),
    )


def distribution_metadata(
    distribution_id: str = "dist-1",
    *,
    configuration_variables: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """``params.metadata`` carrying one distribution payload, keyed by its extension URI."""
    payload = distribution_extension(
        distribution_id, configuration_variables=configuration_variables
    )
    return {
        DISTRIBUTION_EXTENSION_URI_V1: payload.model_dump(
            by_alias=True, mode="json", exclude_none=True
        ),
    }

import logging

import pytest
from pydantic import ValidationError

from aion.core.constants import DISTRIBUTION_EXTENSION_URI_V1
from aion.server.core.middlewares.aion_context import AionContextMiddleware


# !! Test Data Factories !!
def create_distribution_payload(identity_overrides=None):
    """Factory function to build a distribution extension payload as sent by the control plane.

    Field names and shape mirror a payload captured from staging, so this factory
    doubles as the contract check against the published extension spec at
    https://docs.aion.to/a2a/extensions/aion/distribution/1.0.0.
    """
    identity = {
        "kind": "principal",
        "id": "identity-1",
        "identityNetwork": "Aion",
        "identityKind": "Personal",
        "representedUserId": "user-1",
        "organizationId": "org-1",
        "displayName": "Artem Sosnytskyi",
        "userName": "sosnytskyi_artem_dev",
        "agentType": "Personal",
    }
    identity.update(identity_overrides or {})

    return {
        "distribution": {
            "id": "dist-1",
            "endpointType": "A2A",
            "url": "https://example.com/distributions/dist-1/a2a/.well-known/agent-card.json",
            "componentAgentCardUrl": "https://example.com/environments/env-1/a2a/.well-known/agent-card.json",
            "identities": [identity],
        },
        "behavior": {
            "id": "beh-1",
            "behaviorKey": "testGraph",
            "versionId": "v1",
        },
        "environment": {
            "id": "env-1",
            "name": "Development",
            "projectId": "proj-1",
            "deploymentId": "dep-1",
            "configurationVariables": {},
        },
    }


# !! Tests !!
class TestGetDistributionExtension:
    def test_parses_payload_with_identity_network(self, caplog):
        """A fully populated identity parses and logs no warning."""
        metadata = {DISTRIBUTION_EXTENSION_URI_V1: create_distribution_payload()}

        with caplog.at_level(logging.WARNING):
            extension = AionContextMiddleware._get_distribution_extension(metadata)

        assert extension.distribution.identities[0].identity_network == "Aion"
        assert caplog.records == []

    @pytest.mark.parametrize(
        "field",
        ["identityNetwork", "identityKind", "organizationId"],
    )
    def test_rejects_identity_missing_required_field(self, field):
        """The spec marks these identity fields required, so omitting one is an error."""
        payload = create_distribution_payload()
        del payload["distribution"]["identities"][0][field]
        metadata = {DISTRIBUTION_EXTENSION_URI_V1: payload}

        with pytest.raises(ValidationError):
            AionContextMiddleware._get_distribution_extension(metadata)

    @pytest.mark.parametrize(
        "field",
        ["projectId", "deploymentId", "configurationVariables"],
    )
    def test_rejects_environment_missing_required_field(self, field):
        """The spec marks these environment fields required, so omitting one is an error."""
        payload = create_distribution_payload()
        del payload["environment"][field]
        metadata = {DISTRIBUTION_EXTENSION_URI_V1: payload}

        with pytest.raises(ValidationError):
            AionContextMiddleware._get_distribution_extension(metadata)

    def test_binds_every_field_the_control_plane_sends(self):
        """Every field in a real control-plane payload lands on the model.

        Guards against the failure mode where a renamed wire field is silently
        dropped by extra="ignore" and the attribute just reads as None.
        """
        payload = create_distribution_payload()
        metadata = {DISTRIBUTION_EXTENSION_URI_V1: payload}

        extension = AionContextMiddleware._get_distribution_extension(metadata)

        identity = extension.distribution.identities[0]
        assert identity.identity_network == "Aion"
        assert identity.identity_kind == "Personal"
        assert identity.agent_type == "Personal"
        assert identity.represented_user_id == "user-1"
        assert extension.distribution.component_agent_card_url.endswith(
            "/environments/env-1/a2a/.well-known/agent-card.json"
        )
        assert extension.environment.project_id == "proj-1"

    def test_returns_none_without_extension(self):
        """Metadata without the distribution extension yields no payload."""
        assert AionContextMiddleware._get_distribution_extension({}) is None

import logging

from aion.core.constants import DISTRIBUTION_EXTENSION_URI_V1
from aion.server.core.middlewares.aion_context import AionContextMiddleware


# !! Test Data Factories !!
def create_distribution_payload(identity_overrides=None):
    """Factory function to build a distribution extension payload as sent by the control plane."""
    identity = {
        "kind": "principal",
        "id": "identity-1",
        "networkType": "Aion",
        "organizationId": "org-1",
        "agentType": "Personal",
    }
    identity.update(identity_overrides or {})

    return {
        "version": "1.0.0",
        "distribution": {
            "id": "dist-1",
            "endpointType": "Aion",
            "url": "https://example.com/agent",
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
            "deploymentId": "dep-1",
            "configurationVariables": {},
        },
    }


# !! Tests !!
class TestGetDistributionExtension:
    def test_parses_payload_with_network_type(self, caplog):
        """A fully populated identity parses and logs no warning."""
        metadata = {DISTRIBUTION_EXTENSION_URI_V1: create_distribution_payload()}

        with caplog.at_level(logging.WARNING):
            extension = AionContextMiddleware._get_distribution_extension(metadata)

        assert extension.distribution.identities[0].network_type == "Aion"
        assert caplog.records == []

    def test_parses_payload_without_network_type(self, caplog):
        """A missing networkType leaves the field unset instead of rejecting the request."""
        payload = create_distribution_payload()
        del payload["distribution"]["identities"][0]["networkType"]
        metadata = {DISTRIBUTION_EXTENSION_URI_V1: payload}

        with caplog.at_level(logging.WARNING):
            extension = AionContextMiddleware._get_distribution_extension(metadata)

        assert extension.distribution.identities[0].network_type is None

    def test_warns_when_network_type_missing(self, caplog):
        """A missing networkType is surfaced as a warning naming the distribution and identity."""
        payload = create_distribution_payload()
        del payload["distribution"]["identities"][0]["networkType"]
        metadata = {DISTRIBUTION_EXTENSION_URI_V1: payload}

        with caplog.at_level(logging.WARNING):
            AionContextMiddleware._get_distribution_extension(metadata)

        assert len(caplog.records) == 1
        message = caplog.records[0].getMessage()
        assert "dist-1" in message
        assert "identity-1" in message

    def test_returns_none_without_extension(self):
        """Metadata without the distribution extension yields no payload."""
        assert AionContextMiddleware._get_distribution_extension({}) is None

"""Tests for utils/deployment.py."""

import pytest

from aion.server.utils.deployment import (
    generate_a2a_manifest,
    get_api_version,
    get_protocol_version,
    get_service_name,
)
from aion.core.a2a import A2AManifest


class TestConstantAccessors:
    @pytest.mark.parametrize("getter", [get_service_name, get_api_version, get_protocol_version])
    def test_returns_non_empty_string(self, getter):
        """Verify that returns non empty string."""
        result = getter()
        assert isinstance(result, str)
        assert result != ""


class TestGenerateA2AManifest:
    def test_returns_a2a_manifest_instance(self):
        """Verify that returns A2A manifest instance."""
        result = generate_a2a_manifest(["agent-1"], "/{agent_id}/a2a")
        assert isinstance(result, A2AManifest)

    def test_api_version_set_from_getter(self):
        """Verify that API version set from getter."""
        result = generate_a2a_manifest([], "/ignored")
        assert result.api_version == get_api_version()

    def test_name_set_from_service_name(self):
        """Verify that name set from service name."""
        result = generate_a2a_manifest([], "/ignored")
        assert result.name == get_service_name()

    def test_empty_agent_ids_produces_empty_endpoints(self):
        """Verify that empty agent IDs produces empty endpoints."""
        result = generate_a2a_manifest([], "/{agent_id}/a2a")
        assert result.endpoints == {}

    def test_single_agent_endpoint_generated(self):
        """Verify that single agent endpoint generated."""
        result = generate_a2a_manifest(["my-agent"], "/{agent_id}/a2a")
        assert "my-agent" in result.endpoints
        assert result.endpoints["my-agent"] == "/my-agent/a2a"

    def test_multiple_agents_all_present(self):
        """Verify that multiple agents all present."""
        ids = ["alpha", "beta", "gamma"]
        result = generate_a2a_manifest(ids, "/{agent_id}/run")
        assert set(result.endpoints.keys()) == set(ids)

    def test_endpoint_template_substitution(self):
        """Verify that endpoint template substitution."""
        result = generate_a2a_manifest(["svc"], "/api/agents/{agent_id}/a2a")
        assert result.endpoints["svc"] == "/api/agents/svc/a2a"

    def test_duplicate_agent_ids_deduplicated(self):
        """Verify that duplicate agent IDs deduplicated."""
        result = generate_a2a_manifest(["a", "a", "b"], "/{agent_id}/a2a")
        assert len(result.endpoints) == 2

"""Tests for AionAgentCard.from_config()."""

import pytest
from unittest.mock import patch

from aion.server.agent.card import AionAgentCard
from aion.core.config.models import AgentConfig, AgentSkill


DOCS_URL = "https://docs.example.com"


@pytest.fixture(autouse=True)
def mock_docs_url():
    with patch("aion.shared.agent.card.app_settings") as mock_settings:
        mock_settings.docs_url = DOCS_URL
        yield mock_settings


def _make_config(**kwargs) -> AgentConfig:
    defaults = {"path": "my.module:agent"}
    defaults.update(kwargs)
    return AgentConfig(**defaults)


class TestCapabilities:
    def test_streaming_enabled(self):
        """from_config produces a card with streaming capability enabled."""
        card = AionAgentCard.from_config(_make_config(), "http://localhost:8000")
        assert card.capabilities.streaming is True

    def test_push_notifications_enabled(self):
        """from_config produces a card with push_notifications capability enabled."""
        card = AionAgentCard.from_config(_make_config(), "http://localhost:8000")
        assert card.capabilities.push_notifications is True

    def test_two_extensions_registered(self):
        """from_config registers exactly two capability extensions."""
        card = AionAgentCard.from_config(_make_config(), "http://localhost:8000")
        assert len(card.capabilities.extensions) == 2

    def test_extension_uris_use_docs_url(self):
        """All capability extension URIs start with the configured docs URL."""
        card = AionAgentCard.from_config(_make_config(), "http://localhost:8000")
        uris = [ext.uri for ext in card.capabilities.extensions]
        assert all(uri.startswith(DOCS_URL) for uri in uris)

    def test_extensions_not_required(self):
        """All capability extensions have required set to False."""
        card = AionAgentCard.from_config(_make_config(), "http://localhost:8000")
        assert all(ext.required is False for ext in card.capabilities.extensions)


class TestMetadataDefaults:
    def test_default_name(self):
        """Card name equals 'Agent' when no custom name is provided in config."""
        # AgentConfig defaults name to "Agent" (truthy), so card.name == "Agent"
        card = AionAgentCard.from_config(_make_config(), "http://localhost:8000")
        assert card.name == "Agent"

    def test_fallback_name_when_name_empty(self):
        """Card name falls back to 'Graph Agent' when config.name is an empty string."""
        # The `or "Graph Agent"` branch fires only when config.name is falsy
        config = _make_config()
        object.__setattr__(config, "name", "")
        card = AionAgentCard.from_config(config, "http://localhost:8000")
        assert card.name == "Graph Agent"

    def test_default_description(self):
        """Card description defaults to 'Agent based on external graph'."""
        card = AionAgentCard.from_config(_make_config(), "http://localhost:8000")
        assert card.description == "Agent based on external graph"

    def test_default_version(self):
        """Card version defaults to '1.0.0'."""
        card = AionAgentCard.from_config(_make_config(), "http://localhost:8000")
        assert card.version == "1.0.0"


class TestCustomMetadata:
    def test_custom_name(self):
        """Card name reflects the custom name set in AgentConfig."""
        card = AionAgentCard.from_config(
            _make_config(name="My Agent"), "http://localhost:8000"
        )
        assert card.name == "My Agent"

    def test_custom_description(self):
        """Card description reflects the custom description set in AgentConfig."""
        card = AionAgentCard.from_config(
            _make_config(description="Does things"), "http://localhost:8000"
        )
        assert card.description == "Does things"

    def test_custom_version(self):
        """Card version reflects the custom version set in AgentConfig."""
        card = AionAgentCard.from_config(
            _make_config(version="2.3.4"), "http://localhost:8000"
        )
        assert card.version == "2.3.4"


class TestSkills:
    def test_no_skills_produces_empty_list(self):
        """Card has an empty skills list when no skills are configured."""
        card = AionAgentCard.from_config(_make_config(), "http://localhost:8000")
        assert card.skills == []

    def test_skills_are_mapped(self):
        """All configured skills are mapped and present on the card."""
        skills = [
            AgentSkill(id="s1", name="Search", description="web search", tags=["web"], examples=["find X"]),
            AgentSkill(id="s2", name="Summarise"),
        ]
        card = AionAgentCard.from_config(
            _make_config(skills=skills), "http://localhost:8000"
        )
        assert len(card.skills) == 2
        ids = {s.id for s in card.skills}
        assert ids == {"s1", "s2"}

    def test_skill_fields_copied(self):
        """Skill fields (id, name, description, tags, examples) are copied faithfully to the card."""
        skill = AgentSkill(
            id="sk1", name="Skill One", description="desc",
            tags=["t1", "t2"], examples=["ex1"]
        )
        card = AionAgentCard.from_config(
            _make_config(skills=[skill]), "http://localhost:8000"
        )
        mapped = card.skills[0]
        assert mapped.id == "sk1"
        assert mapped.name == "Skill One"
        assert mapped.description == "desc"
        assert mapped.tags == ["t1", "t2"]
        assert mapped.examples == ["ex1"]


class TestSupportedInterfaces:
    def test_two_interfaces_registered(self):
        """from_config registers exactly two supported interfaces."""
        card = AionAgentCard.from_config(_make_config(), "http://localhost:8000")
        assert len(card.supported_interfaces) == 2

    def test_interfaces_use_base_url(self):
        """All supported interfaces use the provided base URL."""
        base_url = "http://my-agent:9000"
        card = AionAgentCard.from_config(_make_config(), base_url)
        for iface in card.supported_interfaces:
            assert iface.url == base_url

    def test_interfaces_use_jsonrpc_binding(self):
        """All supported interfaces use JSONRPC as the protocol binding."""
        card = AionAgentCard.from_config(_make_config(), "http://localhost:8000")
        assert all(i.protocol_binding == "JSONRPC" for i in card.supported_interfaces)

    def test_both_protocol_versions_present(self):
        """Supported interfaces include both protocol versions '1.0' and '0.3'."""
        card = AionAgentCard.from_config(_make_config(), "http://localhost:8000")
        versions = {i.protocol_version for i in card.supported_interfaces}
        assert versions == {"1.0", "0.3"}


class TestInputOutputModes:
    def test_default_input_modes(self):
        """Card default input modes are ['text'] when none are configured."""
        card = AionAgentCard.from_config(_make_config(), "http://localhost:8000")
        assert card.default_input_modes == ["text"]

    def test_custom_input_modes(self):
        """Card default input modes reflect the custom modes set in AgentConfig."""
        card = AionAgentCard.from_config(
            _make_config(input_modes=["text", "audio"]), "http://localhost:8000"
        )
        assert card.default_input_modes == ["text", "audio"]

    def test_default_output_modes(self):
        """Card default output modes are ['text'] when none are configured."""
        card = AionAgentCard.from_config(_make_config(), "http://localhost:8000")
        assert card.default_output_modes == ["text"]

"""Tests for AionAgentCard.from_config()."""

import pytest

from aion.core.a2a import AION_JSONRPC_METHOD_EXTENSION_BINDINGS
from aion.core.constants.a2a import (
    BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1,
    CRON_EXTENSION_URI_V1,
    CONTEXT_EXTENSION_URI_V1,
    DAEMON_EXTENSION_URI_V1,
)
from aion.server.agent.card import AionAgentCard
from aion.core.config.models import AgentConfig, AgentSkill
from aion.core.runtime import aion_a2a_extension_registry


def _make_config(**kwargs) -> AgentConfig:
    defaults = {"path": "my.module:agent"}
    defaults.update(kwargs)
    return AgentConfig(**defaults)


class TestCapabilities:
    @pytest.fixture(autouse=True)
    def _registry(self, isolated_registry):
        pass

    def test_cron_support_is_advertised_once_and_optional(self):
        """The typed Cron collector is discoverable without requiring its use."""
        card = AionAgentCard.from_config(_make_config(), "http://localhost:8000")
        declarations = [
            extension for extension in card.capabilities.extensions
            if extension.uri == CRON_EXTENSION_URI_V1
        ]
        assert len(declarations) == 1
        assert not declarations[0].required

    def test_the_context_extension_is_advertised_once_and_optional(self):
        """The unified Context extension is declared once, never required.

        Its methods are invoked directly and need no activation, so a client
        that ignores the declaration loses nothing.
        """
        card = AionAgentCard.from_config(_make_config(), "http://localhost:8000")
        declarations = [
            extension for extension in card.capabilities.extensions
            if extension.uri == CONTEXT_EXTENSION_URI_V1
        ]
        assert len(declarations) == 1
        assert not declarations[0].required

    def test_evolution_is_not_advertised_before_it_is_enabled(self):
        """The counter-example: advertised=True, and still off the card.

        Advertisement and enablement are independent in both directions, so
        the card filter has to read both - not infer one from the other.
        """
        descriptors = {d.uri: d for d in aion_a2a_extension_registry.get_all()}
        assert descriptors[BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1].advertised is True

        card = AionAgentCard.from_config(_make_config(), "http://localhost:8000")

        assert BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1 not in {
            ext.uri for ext in card.capabilities.extensions
        }

    def test_evolution_is_advertised_once_enabled(self):
        """Enabling it through enabled_extensions is what puts it on the card.

        With the daemon extension it requires, because without it the card
        would be offering something every request is refused for - see the
        test below.
        """
        aion_a2a_extension_registry.activate(
            [BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1, DAEMON_EXTENSION_URI_V1]
        )

        card = AionAgentCard.from_config(_make_config(), "http://localhost:8000")

        assert BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1 in {
            ext.uri for ext in card.capabilities.extensions
        }

    def test_an_extension_missing_a_required_extension_is_not_advertised(self):
        """`requires` is a condition on advertising, not only on verification.

        `activate()` is additive and expands nothing, so an agent listing
        evolution alone in enabled_extensions leaves the daemon extension it
        requires inactive. Every request declaring evolution is then refused
        for the missing co-activation, and a card advertising it would have
        promised exactly that call.
        """
        aion_a2a_extension_registry.activate([BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1])
        descriptors = {d.uri: d for d in aion_a2a_extension_registry.get_all()}
        assert descriptors[BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1].active is True
        assert descriptors[DAEMON_EXTENSION_URI_V1].active is False

        card = AionAgentCard.from_config(_make_config(), "http://localhost:8000")

        assert BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1 not in {
            ext.uri for ext in card.capabilities.extensions
        }

    def test_an_enabled_but_unavailable_extension_is_not_advertised(self):
        """Advertising what this deployment would refuse invites a failed call.

        The evolution toolkit is the real case: the agent enabled the
        extension, the deployment cannot run it, and the request-time verifier
        already rejects it with this exact reason.
        """
        aion_a2a_extension_registry.activate(
            [BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1, DAEMON_EXTENSION_URI_V1]
        )
        aion_a2a_extension_registry.mark_unavailable(
            BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1, "the evolution toolkit is not installed"
        )

        card = AionAgentCard.from_config(_make_config(), "http://localhost:8000")

        assert BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1 not in {
            ext.uri for ext in card.capabilities.extensions
        }

    def test_streaming_enabled(self):
        """from_config produces a card with streaming capability enabled."""
        card = AionAgentCard.from_config(_make_config(), "http://localhost:8000")
        assert card.capabilities.streaming is True

    def test_push_notifications_enabled(self):
        """from_config produces a card with push_notifications capability enabled."""
        card = AionAgentCard.from_config(_make_config(), "http://localhost:8000")
        assert card.capabilities.push_notifications is True

    def test_a_method_extension_may_be_advertised(self):
        """Being a method extension is not what keeps one off the card.

        Asserted on a URI that really is bound to a JSON-RPC method, not on a
        fake descriptor that merely stands for one: an invented URI no
        binding claims would pass this test however the card treated method
        extensions, which is the one outcome that proves nothing. Nothing in
        the card builder knows a method extension from any other - the property
        this pins, so the distinction cannot quietly grow into a rule.
        """
        uri = AION_JSONRPC_METHOD_EXTENSION_BINDINGS["GetContext"].extension_uri
        assert uri == CONTEXT_EXTENSION_URI_V1

        card = AionAgentCard.from_config(_make_config(), "http://localhost:8000")

        assert uri in {ext.uri for ext in card.capabilities.extensions}

    def test_the_card_advertises_exactly_what_the_registry_says_it_may(self):
        """The card is get_advertised() and nothing else.

        Asserted against the registry rather than a fixed count: which
        extensions ship is the registry's business, and a card that quietly
        stopped advertising one — or advertised one the registry withheld,
        which a client would then be entitled to invoke — is the failure worth
        catching. Deciding the rule here instead of in the card builder is the
        point: one place answers "may this be published".
        """
        card = AionAgentCard.from_config(_make_config(), "http://localhost:8000")
        expected = {ext.uri for ext in aion_a2a_extension_registry.get_advertised()}

        assert {ext.uri for ext in card.capabilities.extensions} == expected
        assert expected, "registry advertises nothing; the assertion above is vacuous"

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
        """Card default input modes are plain text when none are configured."""
        card = AionAgentCard.from_config(_make_config(), "http://localhost:8000")
        assert card.default_input_modes == ["text/plain"]

    def test_custom_input_modes(self):
        """The card names the configured modes by their media types."""
        card = AionAgentCard.from_config(
            _make_config(input_modes=["text", "audio", "image/png"]), "http://localhost:8000"
        )
        assert card.default_input_modes == ["text/plain", "audio/*", "image/png"]

    def test_default_output_modes(self):
        """Card default output modes are plain text when none are configured."""
        card = AionAgentCard.from_config(_make_config(), "http://localhost:8000")
        assert card.default_output_modes == ["text/plain"]


def test_the_card_asks_for_a_bearer_token():
    """Every call needs a token, and the card says so."""
    from google.protobuf.json_format import MessageToDict

    card = MessageToDict(AionAgentCard.from_config(_make_config(), "http://localhost:8000"))

    scheme = card["securitySchemes"]["bearer"]["httpAuthSecurityScheme"]
    assert (scheme["scheme"], scheme["bearerFormat"]) == ("Bearer", "JWT")
    # Never published: its empty scope list serializes as {}, which the
    # platform's card decoder refuses, failing registration.
    assert "securityRequirements" not in card

"""Tests for config/models.py — AgentConfig and AionConfig validators."""

import pytest
from pydantic import ValidationError

from aion.core.config.models import AgentConfig, AgentSkill, AionConfig, ConfigurationField, ConfigurationType


class TestAgentConfigVersion:
    def test_valid_version(self):
        """AgentConfig accepts a valid semantic version string."""
        agent = AgentConfig(path="my.module:Agent", version="2.3.4")
        assert agent.version == "2.3.4"

    def test_invalid_version_raises(self):
        """AgentConfig raises ValidationError for a version string not matching X.Y.Z."""
        with pytest.raises(ValidationError, match="X.Y.Z"):
            AgentConfig(path="my.module:Agent", version="1.0")

    def test_version_with_letters_raises(self):
        """AgentConfig raises ValidationError for a version string with a leading letter."""
        with pytest.raises(ValidationError):
            AgentConfig(path="my.module:Agent", version="v1.0.0")


class TestAgentConfigModes:
    def test_default_modes_are_text(self):
        """AgentConfig defaults input and output modes to ['text']."""
        agent = AgentConfig(path="my.module:Agent")
        assert agent.input_modes == ["text"]
        assert agent.output_modes == ["text"]

    def test_valid_non_default_mode(self):
        """AgentConfig accepts valid non-default mode names like 'audio' and 'image'."""
        agent = AgentConfig(path="my.module:Agent", input_modes=["audio", "image"])
        assert set(agent.input_modes) == {"audio", "image"}

    def test_invalid_mode_raises(self):
        """AgentConfig raises ValidationError with 'Invalid mode' for unsupported mode names."""
        with pytest.raises(ValidationError, match="Invalid mode"):
            AgentConfig(path="my.module:Agent", input_modes=["pdf"])

    def test_empty_modes_raises(self):
        """AgentConfig raises ValidationError when modes list is empty."""
        with pytest.raises(ValidationError):
            AgentConfig(path="my.module:Agent", input_modes=[])


class TestAgentConfigSkills:
    def test_unique_skill_ids_accepted(self):
        """AgentConfig accepts a list of skills with unique IDs."""
        skills = [
            AgentSkill(id="skill-a", name="A"),
            AgentSkill(id="skill-b", name="B"),
        ]
        agent = AgentConfig(path="my.module:Agent", skills=skills)
        assert len(agent.skills) == 2

    def test_duplicate_skill_ids_raises(self):
        """AgentConfig raises ValidationError with 'unique' for skills with duplicate IDs."""
        skills = [
            AgentSkill(id="skill-a", name="First"),
            AgentSkill(id="skill-a", name="Duplicate"),
        ]
        with pytest.raises(ValidationError, match="unique"):
            AgentConfig(path="my.module:Agent", skills=skills)


class TestAgentConfigConfiguration:
    def test_none_value_becomes_default_field(self):
        """AgentConfig converts None configuration values to a default ConfigurationField."""
        agent = AgentConfig(path="my.module:Agent", configuration={"key": None})
        assert isinstance(agent.configuration["key"], ConfigurationField)

    def test_dict_value_converted_to_configuration_field(self):
        """AgentConfig converts a dict configuration value to a ConfigurationField instance."""
        agent = AgentConfig(
            path="my.module:Agent",
            configuration={"key": {"type": "integer", "required": True}},
        )
        field = agent.configuration["key"]
        assert isinstance(field, ConfigurationField)
        assert field.type == ConfigurationType.INTEGER
        assert field.required is True

    def test_invalid_field_type_raises(self):
        """AgentConfig raises ValidationError for configuration values that are not dicts or fields."""
        with pytest.raises(ValidationError):
            AgentConfig(path="my.module:Agent", configuration={"key": 42})


class TestAionConfig:
    def test_dict_of_agent_configs(self):
        """AionConfig accepts a dict of agent config dicts keyed by agent id."""
        config = AionConfig(agents={"agent1": {"path": "my.module:Agent"}})
        assert "agent1" in config.agents

    def test_list_of_agents_converted_to_dict(self):
        """AionConfig converts a list of agent configs to a dict using name as key."""
        # List format uses name as key; unnamed agents fall back to agent_N
        config = AionConfig(agents=[{"path": "my.module:Agent", "name": "MyAgent"}])
        assert "MyAgent" in config.agents

    def test_list_with_default_name_uses_index_key(self):
        """AionConfig uses 'agent_0' as the key for a list item with the default name."""
        config = AionConfig(agents=[{"path": "my.module:Agent"}])
        assert "agent_0" in config.agents

    def test_get_agent_returns_config(self):
        """get_agent returns the AgentConfig for a known id and None for an unknown id."""
        config = AionConfig(agents={"a1": {"path": "my.module:A"}})
        assert config.get_agent("a1") is not None
        assert config.get_agent("missing") is None

    def test_list_agents_returns_keys(self):
        """list_agents returns all agent keys in the config."""
        config = AionConfig(agents={"a": {"path": "x"}, "b": {"path": "y"}})
        assert set(config.list_agents()) == {"a", "b"}

    def test_invalid_agents_type_raises(self):
        """AionConfig raises ValidationError when agents is not a dict or list."""
        with pytest.raises(ValidationError):
            AionConfig(agents="not-a-dict-or-list")  # type: ignore[arg-type]


class TestConfigurationFieldItems:
    def test_array_items_accepted_as_dict(self):
        """ConfigurationField accepts a dict as items for an array type."""
        field = ConfigurationField(type=ConfigurationType.ARRAY, items={"type": "string"})
        assert isinstance(field.items, ConfigurationField)

    def test_object_items_accepted_as_dict_of_fields(self):
        """ConfigurationField accepts a dict of field specs as items for an object type."""
        field = ConfigurationField(
            type=ConfigurationType.OBJECT,
            items={"name": {"type": "string"}, "age": {"type": "integer"}},
        )
        assert isinstance(field.items, dict)
        assert "name" in field.items

    def test_items_on_non_array_or_object_raises(self):
        """ConfigurationField raises ValidationError when items is set on a non-array/object type."""
        with pytest.raises(ValidationError):
            ConfigurationField(type=ConfigurationType.STRING, items={"type": "string"})

    def test_array_items_as_configuration_field_instance(self):
        """ConfigurationField accepts a ConfigurationField instance as items for an array type."""
        item_field = ConfigurationField(type=ConfigurationType.INTEGER)
        field = ConfigurationField(type=ConfigurationType.ARRAY, items=item_field)
        assert isinstance(field.items, ConfigurationField)
        assert field.items.type == ConfigurationType.INTEGER

    def test_object_items_as_configuration_field_instances(self):
        """ConfigurationField accepts ConfigurationField instances as values in an object items dict."""
        field = ConfigurationField(
            type=ConfigurationType.OBJECT,
            items={"x": ConfigurationField(type=ConfigurationType.FLOAT)},
        )
        assert isinstance(field.items, dict)
        assert isinstance(field.items["x"], ConfigurationField)

    def test_object_items_none_value_becomes_default_field(self):
        """ConfigurationField converts a None value in an object items dict to a default field."""
        field = ConfigurationField(
            type=ConfigurationType.OBJECT,
            items={"x": None},
        )
        assert isinstance(field.items["x"], ConfigurationField)

    def test_object_items_invalid_value_raises(self):
        """ConfigurationField raises ValidationError for non-field values in an object items dict."""
        with pytest.raises(ValidationError):
            ConfigurationField(
                type=ConfigurationType.OBJECT,
                items={"x": 42},
            )


class TestConfigurationFieldConstraints:
    def test_all_constraint_fields_stored(self):
        """ConfigurationField stores all constraint fields (min, max, lengths, enum, required, nullable, default)."""
        field = ConfigurationField(
            type=ConfigurationType.INTEGER,
            min=0,
            max=100,
            min_length=1,
            max_length=255,
            enum=["a", "b", "c"],
            required=True,
            nullable=False,
            default="hello",
        )
        assert field.min == 0
        assert field.max == 100
        assert field.min_length == 1
        assert field.max_length == 255
        assert field.enum == ["a", "b", "c"]
        assert field.required is True
        assert field.nullable is False
        assert field.default == "hello"


class TestConfigurationFieldModelDump:
    def test_dump_array_field_serializes_items(self):
        """model_dump for an array field serializes items as a nested dict."""
        field = ConfigurationField(
            type=ConfigurationType.ARRAY,
            items={"type": "integer"},
        )
        data = field.model_dump()
        assert data["type"] == "array"
        assert isinstance(data["items"], dict)

    def test_dump_object_field_serializes_items(self):
        """model_dump for an object field serializes items as a dict containing field specs."""
        field = ConfigurationField(
            type=ConfigurationType.OBJECT,
            items={"name": {"type": "string"}},
        )
        data = field.model_dump()
        assert isinstance(data["items"], dict)
        assert "name" in data["items"]

    def test_dump_without_items_has_none_items(self):
        """model_dump for a field without items has None for the items key."""
        field = ConfigurationField(type=ConfigurationType.STRING)
        data = field.model_dump()
        assert data["items"] is None


class TestAgentConfigConfigurationEdgeCases:
    def test_configuration_none_becomes_empty_dict(self):
        """AgentConfig converts a None configuration to an empty dict."""
        agent = AgentConfig(path="m:A", configuration=None)
        assert agent.configuration == {}

    def test_configuration_non_dict_becomes_empty_dict(self):
        """AgentConfig silently converts a non-dict configuration to an empty dict."""
        # Validator silently converts non-dict to empty dict
        agent = AgentConfig(path="m:A", configuration="not-a-dict")  # type: ignore
        assert agent.configuration == {}

    def test_configuration_field_instance_accepted_directly(self):
        """AgentConfig accepts a ConfigurationField instance directly in the configuration dict."""
        cf = ConfigurationField(type=ConfigurationType.INTEGER, required=True)
        agent = AgentConfig(path="m:A", configuration={"count": cf})
        assert agent.configuration["count"] is cf

    def test_configuration_invalid_value_raises(self):
        """AgentConfig raises ValidationError for a configuration value that is an integer."""
        with pytest.raises(ValidationError):
            AgentConfig(path="m:A", configuration={"k": 123})


class TestAionConfigAgentsEdgeCases:
    def test_agents_none_becomes_empty_dict(self):
        """AionConfig converts None agents to an empty dict."""
        config = AionConfig(agents=None)  # type: ignore
        assert config.agents == {}

    def test_agents_dict_with_agent_config_instance(self):
        """AionConfig accepts an AgentConfig instance directly as a dict value."""
        agent = AgentConfig(path="m:A", name="MyAgent")
        config = AionConfig(agents={"my": agent})
        assert config.agents["my"] is agent

    def test_agents_dict_invalid_value_type_raises(self):
        """AionConfig raises ValidationError with 'must be an AgentConfig' for non-AgentConfig dict values."""
        with pytest.raises(ValidationError, match="must be an AgentConfig"):
            AionConfig(agents={"bad": 42})  # type: ignore

    def test_agents_dict_invalid_agent_config_raises(self):
        """AionConfig raises ValidationError with 'Invalid agent config' for a dict with invalid agent fields."""
        with pytest.raises(ValidationError, match="Invalid agent config"):
            AionConfig(agents={"bad": {"version": "not-semver"}})

    def test_agents_list_with_agent_config_instances(self):
        """AionConfig converts a list of AgentConfig instances to a dict keyed by name."""
        a1 = AgentConfig(path="m:A", name="Alpha")
        a2 = AgentConfig(path="m:B", name="Beta")
        config = AionConfig(agents=[a1, a2])
        assert "Alpha" in config.agents
        assert "Beta" in config.agents

    def test_agents_list_multiple_default_names_use_index_keys(self):
        """AionConfig uses 'agent_N' keys for list items with the default 'Agent' name."""
        # When name=="Agent" (default), falls back to agent_{i}
        config = AionConfig(agents=[
            {"path": "m:A"},
            {"path": "m:B"},
        ])
        assert "agent_0" in config.agents
        assert "agent_1" in config.agents

    def test_agents_list_invalid_item_type_raises(self):
        """AionConfig raises ValidationError with 'must be an AgentConfig' for non-dict list items."""
        with pytest.raises(ValidationError, match="must be an AgentConfig"):
            AionConfig(agents=[42])  # type: ignore

    def test_agents_list_invalid_config_raises(self):
        """AionConfig raises ValidationError with 'Invalid agent config at index' for invalid list items."""
        with pytest.raises(ValidationError, match="Invalid agent config at index"):
            AionConfig(agents=[{"version": "bad"}])

    def test_empty_agents_dict(self):
        """AionConfig with an empty agents dict has no agents and list_agents returns []."""
        config = AionConfig(agents={})
        assert config.agents == {}
        assert config.list_agents() == []

    def test_get_agent_from_multiple(self):
        """get_agent returns the correct AgentConfig for each key and None for missing keys."""
        config = AionConfig(agents={
            "a": {"path": "m:A"},
            "b": {"path": "m:B"},
        })
        assert config.get_agent("a").path == "m:A"
        assert config.get_agent("b").path == "m:B"
        assert config.get_agent("c") is None

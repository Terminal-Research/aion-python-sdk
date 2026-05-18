"""Tests for AgentConfigurationCollector.collect()."""

import pytest
from aion.shared.config import ConfigurationField, ConfigurationType
from aion.shared.config.collectors.agent_configuration import AgentConfigurationCollector


def _collect(cfg: dict) -> dict:
    return AgentConfigurationCollector(cfg).collect()


class TestCollectEmpty:
    def test_empty_dict_returns_empty(self):
        """collect on an empty dict returns an empty dict."""
        assert _collect({}) == {}

    def test_none_equivalent_falsy_returns_empty(self):
        """collect on a None-equivalent input returns an empty dict."""
        assert AgentConfigurationCollector(None).collect() == {}


class TestCollectStringField:
    def test_base_fields_present(self):
        """collect includes type, description, required, and nullable for a string field."""
        field = ConfigurationField(type=ConfigurationType.STRING, description="A name", required=True)
        result = _collect({"name": field})["name"]
        assert result["type"] == "string"
        assert result["description"] == "A name"
        assert result["required"] is True
        assert result["nullable"] is True  # default

    def test_string_specific_fields_included(self):
        """collect includes min_length, max_length, and enum for a string field that has them."""
        field = ConfigurationField(
            type=ConfigurationType.STRING,
            min_length=2,
            max_length=50,
            enum=["foo", "bar"],
        )
        result = _collect({"x": field})["x"]
        assert result["min_length"] == 2
        assert result["max_length"] == 50
        assert result["enum"] == ["foo", "bar"]

    def test_string_field_has_no_min_max(self):
        """collect does not include min/max keys for a string field."""
        field = ConfigurationField(type=ConfigurationType.STRING)
        result = _collect({"x": field})["x"]
        assert "min" not in result
        assert "max" not in result


class TestCollectIntegerField:
    def test_integer_specific_fields_included(self):
        """collect includes type, min, max, and enum for an integer field."""
        field = ConfigurationField(type=ConfigurationType.INTEGER, min=1, max=100, enum=["1", "2"])
        result = _collect({"count": field})["count"]
        assert result["type"] == "integer"
        assert result["min"] == 1
        assert result["max"] == 100
        assert result["enum"] == ["1", "2"]

    def test_integer_has_no_length_fields(self):
        """collect does not include min_length or max_length for an integer field."""
        field = ConfigurationField(type=ConfigurationType.INTEGER)
        result = _collect({"n": field})["n"]
        assert "min_length" not in result
        assert "max_length" not in result


class TestCollectFloatField:
    def test_float_specific_fields_included(self):
        """collect includes type, min, and max for a float field."""
        field = ConfigurationField(type=ConfigurationType.FLOAT, min=0.0, max=1.0)
        result = _collect({"ratio": field})["ratio"]
        assert result["type"] == "float"
        assert result["min"] == 0.0
        assert result["max"] == 1.0


class TestCollectBooleanField:
    def test_boolean_only_has_base_fields(self):
        """collect omits numeric and string constraint fields for a boolean field."""
        field = ConfigurationField(type=ConfigurationType.BOOLEAN, default=True)
        result = _collect({"flag": field})["flag"]
        assert result["type"] == "boolean"
        assert result["default"] is True
        assert "min" not in result
        assert "max" not in result
        assert "min_length" not in result
        assert "enum" not in result
        assert "items" not in result


class TestCollectArrayField:
    def test_array_with_items_schema(self):
        """collect includes a nested items schema for an array field with items."""
        item_field = ConfigurationField(type=ConfigurationType.STRING, min_length=1)
        field = ConfigurationField(type=ConfigurationType.ARRAY, items=item_field)
        result = _collect({"tags": field})["tags"]
        assert result["type"] == "array"
        assert result["items"]["type"] == "string"
        assert result["items"]["min_length"] == 1

    def test_array_without_items_is_none(self):
        """collect sets items to None for an array field with no items schema."""
        field = ConfigurationField(type=ConfigurationType.ARRAY)
        result = _collect({"xs": field})["xs"]
        assert result["items"] is None

    def test_array_has_length_fields(self):
        """collect includes min_length and max_length for an array field that has them."""
        field = ConfigurationField(type=ConfigurationType.ARRAY, min_length=1, max_length=10)
        result = _collect({"xs": field})["xs"]
        assert result["min_length"] == 1
        assert result["max_length"] == 10


class TestCollectObjectField:
    def test_object_with_items_dict(self):
        """collect serializes nested field definitions within an object field's items dict."""
        field = ConfigurationField(
            type=ConfigurationType.OBJECT,
            items={
                "host": ConfigurationField(type=ConfigurationType.STRING),
                "port": ConfigurationField(type=ConfigurationType.INTEGER),
            },
        )
        result = _collect({"db": field})["db"]
        assert result["type"] == "object"
        assert result["items"]["host"]["type"] == "string"
        assert result["items"]["port"]["type"] == "integer"

    def test_object_without_items_is_none(self):
        """collect sets items to None for an object field with no nested schema."""
        field = ConfigurationField(type=ConfigurationType.OBJECT)
        result = _collect({"obj": field})["obj"]
        assert result["items"] is None


class TestCollectDictInput:
    def test_valid_dict_converted_to_field(self):
        """collect converts a valid dict input to a ConfigurationField and serializes it."""
        result = _collect({"x": {"type": "string", "description": "hello", "required": True}})["x"]
        assert result["type"] == "string"
        assert result["description"] == "hello"
        assert result["required"] is True

    def test_invalid_dict_falls_back_to_default(self):
        """collect falls back to a default string field for a dict with unrecognized keys."""
        result = _collect({"x": {"invalid_key": "bad"}})["x"]
        assert result["type"] == "string"
        assert result["required"] is False
        assert result["nullable"] is True


class TestCollectUnknownInput:
    def test_non_field_non_dict_gets_default_config(self):
        """collect uses a default string ConfigurationField for non-field, non-dict inputs."""
        result = _collect({"x": 42})["x"]
        assert result["type"] == "string"
        assert result["description"] == ""
        assert result["default"] is None
        assert result["required"] is False
        assert result["nullable"] is True
        assert result["min_length"] is None
        assert result["max_length"] is None
        assert result["enum"] is None

    def test_multiple_fields_all_processed(self):
        """collect processes all fields in a mixed-type configuration dict."""
        result = _collect({
            "name": ConfigurationField(type=ConfigurationType.STRING),
            "count": ConfigurationField(type=ConfigurationType.INTEGER),
            "bad": "not-a-field",
        })
        assert set(result.keys()) == {"name", "count", "bad"}
        assert result["name"]["type"] == "string"
        assert result["count"]["type"] == "integer"
        assert result["bad"]["type"] == "string"  # default fallback

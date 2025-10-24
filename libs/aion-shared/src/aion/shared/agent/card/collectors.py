from typing import Dict, Any, Union

from aion.shared.config import ConfigurationField, ConfigurationType


class AgentCardConfigurationCollector:
    """
    Collects and processes agent configuration data for AionAgentCard.

    This class takes agent configuration data and converts it into a standardized
    JSON format suitable for AionAgentCard. It handles various field types including
    strings, integers, floats, booleans, arrays, and objects, extracting their
    configuration parameters and validation rules.

    Args:
    agent_configuration (dict): Dictionary containing agent configuration data.
                             Keys are field names, values can be ConfigurationField
                             instances, dictionaries, or other types.
    """

    def __init__(self, agent_configuration: dict):
        self.agent_configuration = agent_configuration

    def collect(self) -> Dict[str, Dict[str, Any]]:
        """Collects configuration and returns JSON for AionAgentCard."""
        if not self.agent_configuration:
            return {}

        result = {}

        for field_name, field_config in self.agent_configuration.items():
            if isinstance(field_config, ConfigurationField):
                result[field_name] = self._extract_field_config(field_config)
            elif isinstance(field_config, dict):
                # If dict is passed, convert it to ConfigurationField
                try:
                    config_field = ConfigurationField(**field_config)
                    result[field_name] = self._extract_field_config(config_field)
                except Exception:
                    # If ConfigurationField creation failed, create default config
                    result[field_name] = self._create_default_config()
            else:
                # For other types create default configuration
                result[field_name] = self._create_default_config()

        return result

    def _extract_field_config(self, field: ConfigurationField) -> Dict[str, Any]:
        """Extracts all possible fields from ConfigurationField based on type."""
        field_type = field.type.value if field.type else "string"

        # Base configuration for all types
        config = {
            "type": field_type,
            "description": field.description or "",
            "default": field.default,
            "required": field.required,
            "nullable": field.nullable
        }

        # Add type-specific fields
        if field_type in ["string"]:
            config.update({
                "min_length": field.min_length,
                "max_length": field.max_length,
                "enum": field.enum
            })

        elif field_type in ["integer", "float"]:
            config.update({
                "min": field.min,
                "max": field.max,
                "enum": field.enum
            })

        elif field_type == "boolean":
            # Boolean only has base fields
            pass

        elif field_type == "array":
            config.update({
                "min_length": field.min_length,
                "max_length": field.max_length,
                "items": self._extract_items_config(field.items, field.type) if field.items else None
            })

        elif field_type == "object":
            config.update({
                "items": self._extract_items_config(field.items, field.type) if field.items else None
            })

        return config

    def _extract_items_config(
            self, items: Union[ConfigurationField, Dict[str, ConfigurationField]],
            field_type: ConfigurationType
    ) -> Union[Dict[str, Any], Dict[str, Dict[str, Any]], None]:
        """Extracts items configuration for arrays and objects."""
        if field_type == ConfigurationType.ARRAY and isinstance(items, ConfigurationField):
            return self._extract_field_config(items)
        elif field_type == ConfigurationType.OBJECT and isinstance(items, dict):
            return {
                key: self._extract_field_config(field_config)
                for key, field_config in items.items()
            }
        return None

    def _create_default_config(self) -> Dict[str, Any]:
        """Creates default configuration for unrecognized types."""
        return {
            "type": "string",
            "description": "",
            "default": None,
            "required": False,
            "nullable": True,
            "min_length": None,
            "max_length": None,
            "enum": None
        }

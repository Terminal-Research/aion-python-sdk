from enum import Enum
from typing import List, Dict, Any, Optional, Union
from pydantic import BaseModel, Field, field_validator


class ConfigurationType(str, Enum):
    """Supported configuration types."""
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    ARRAY = "array"
    OBJECT = "object"


class ConfigurationField(BaseModel):
    """Schema for a single configuration field with support for nested objects."""
    description: str = Field(default="", description="Description of the configuration field")
    default: Optional[Any] = None
    required: bool = Field(default=False, description="Whether this field is required")
    nullable: bool = Field(default=True, description="Whether this field can be null")
    type: Optional[ConfigurationType] = Field(
        default=ConfigurationType.STRING,
        description="Type of the configuration field")

    # Numeric constraints
    min: Optional[Union[int, float]] = Field(default=None, description="Minimum value for numeric types")
    max: Optional[Union[int, float]] = Field(default=None, description="Maximum value for numeric types")

    # String/Array length constraints
    min_length: Optional[int] = Field(default=None, description="Minimum length for strings/arrays")
    max_length: Optional[int] = Field(default=None, description="Maximum length for strings/arrays")

    # Enum validation
    enum: Optional[List[str]] = Field(default=None, description="List of allowed values")

    # Unified validation for arrays and objects
    items: Optional[Union['ConfigurationField', Dict[str, 'ConfigurationField']]] = Field(
        default=None,
        description="For arrays: schema of array items. For objects: dictionary of property schemas")

    @field_validator('items', mode='before')
    @classmethod
    def validate_items(cls, value, info):
        """Validate items configuration for arrays and objects."""
        if value is None:
            return None

        field_type = info.data.get('type')

        if field_type == ConfigurationType.ARRAY:
            # For arrays, items should be a single ConfigurationField
            if isinstance(value, ConfigurationField):
                return value
            elif isinstance(value, dict):
                try:
                    return ConfigurationField(**value)
                except Exception as e:
                    raise ValueError(f"Invalid array item schema: {e}")
            else:
                raise ValueError("Array items must be a ConfigurationField instance or dict")

        elif field_type == ConfigurationType.OBJECT:
            # For objects, items should be a dictionary of ConfigurationField
            if not isinstance(value, dict):
                raise ValueError("Object items must be a dictionary")

            result = {}
            for key, prop_value in value.items():
                if isinstance(prop_value, ConfigurationField):
                    result[key] = prop_value
                elif isinstance(prop_value, dict):
                    try:
                        result[key] = ConfigurationField(**prop_value)
                    except Exception as e:
                        raise ValueError(f"Invalid property field '{key}': {e}")
                elif prop_value is None:
                    result[key] = ConfigurationField()
                else:
                    raise ValueError(f"Property '{key}' must be a ConfigurationField instance, dict, or None")

            return result
        else:
            # For other types, items should not be used
            if value is not None:
                raise ValueError(f"Items field is only valid for array and object types, got {field_type}")
            return None

    def model_dump(self, **kwargs) -> Dict[str, Any]:
        """Custom model dump that handles nested ConfigurationField objects."""
        data = super().model_dump(**kwargs)

        # Convert nested ConfigurationField objects in items
        if data.get('items'):
            if isinstance(data['items'], dict):
                # Object type - convert each property
                items_dict = {}
                for key, field in data['items'].items():
                    if isinstance(field, ConfigurationField):
                        items_dict[key] = field.model_dump(**kwargs)
                    else:
                        items_dict[key] = field
                data['items'] = items_dict
            elif isinstance(data['items'], ConfigurationField):
                # Array type - convert the item schema
                data['items'] = data['items'].model_dump(**kwargs)

        return data


class AgentCapabilities(BaseModel):
    """Agent capabilities configuration."""
    streaming: bool = Field(default=False, description="Whether agent supports streaming responses")
    pushNotifications: bool = Field(default=False, description="Whether agent supports push notifications")


class AgentSkill(BaseModel):
    """Agent skill configuration."""
    id: str = Field(..., description="Unique identifier for the skill")
    name: str = Field(..., description="Human-readable name of the skill")
    description: str = Field(default="", description="Detailed description of the skill")
    tags: List[str] = Field(default_factory=list, description="Tags categorizing the skill")
    examples: List[str] = Field(default_factory=list, description="Example usage patterns")


class AgentConfig(BaseModel):
    """Configuration for an agent."""

    # Required fields
    path: str = Field(..., description="Path to agent class, function, or graph instance")

    # Optional metadata with defaults
    name: str = Field(
        default="Agent",
        description="Human-readable name of the agent")

    description: str = Field(
        default="",
        description="Detailed description of the agent")

    version: str = Field(
        default="1.0.0",
        description="Agent version")

    # Capabilities and features
    capabilities: AgentCapabilities = Field(
        default_factory=AgentCapabilities,
        description="Agent capabilities")

    skills: List[AgentSkill] = Field(
        default_factory=list,
        description="List of agent skills")

    # Input/output modes
    input_modes: List[str] = Field(
        default_factory=lambda: ["text"],
        description="Supported input modes")

    output_modes: List[str] = Field(
        default_factory=lambda: ["text"],
        description="Supported output modes")

    # Additional configuration
    configuration: Dict[str, ConfigurationField] = Field(
        default_factory=dict,
        description="Additional configuration parameters")

    @field_validator('configuration', mode='before')
    @classmethod
    def validate_configuration(cls, value):
        """Validate configuration dictionary and create default ConfigurationField for None values."""
        if value is None:
            return {}

        if not isinstance(value, dict):
            return {}

        result = {}
        for key, key_value in value.items():
            if key_value is None:
                # Create default ConfigurationField when value is None
                result[key] = ConfigurationField()
            elif isinstance(key_value, ConfigurationField):
                result[key] = key_value
            elif isinstance(key_value, dict):
                # If it's already a dict, validate it can create ConfigurationField
                try:
                    config_field = ConfigurationField(**key_value)
                    result[key] = config_field
                except Exception as e:
                    raise ValueError(f"Invalid configuration field '{key}': {e}")
            else:
                raise ValueError(f"Configuration field '{key}' must be a ConfigurationField instance, dict, or None")

        return result

    @field_validator('input_modes', 'output_modes')
    @classmethod
    def validate_modes(cls, value):
        """Validate input/output modes."""
        if not value:
            raise ValueError("At least one mode must be specified")

        valid_modes = {"text", "audio", "image", "video", "json"}
        for mode in value:
            if mode not in valid_modes:
                raise ValueError(f"Invalid mode: {mode}. Must be one of {valid_modes}")
        return value

    @field_validator('version')
    @classmethod
    def validate_version(cls, value):
        """Validate version format."""
        import re
        if not re.match(r'^\d+\.\d+\.\d+$', value):
            raise ValueError("Version must be in format X.Y.Z")
        return value

    @field_validator('skills')
    @classmethod
    def validate_skills(cls, value):
        """Validate skills have unique IDs."""
        if value:
            skill_ids = [skill.id for skill in value]
            if len(skill_ids) != len(set(skill_ids)):
                raise ValueError("Skill IDs must be unique")
        return value

    @classmethod
    def create_minimal_config(cls, agent_id: str, item_type: str = "graph") -> "AgentConfig":
        """Create a minimal configuration for an agent."""
        return cls(
            path="",  # Will be set by caller
            name=f'{agent_id.replace("-", " ").title()} Agent',
            description=f'Agent created from {item_type}',
            version='1.0.0',
            capabilities=AgentCapabilities(),
            skills=[],
            input_modes=['text'],
            output_modes=['text'],
            configuration={}
        )

    class Config:
        """Pydantic configuration."""
        extra = "forbid"  # Don't allow extra fields in agent config root level
        use_enum_values = True
        validate_assignment = True

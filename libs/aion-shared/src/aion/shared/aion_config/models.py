from enum import Enum
from typing import List, Dict, Any, Optional, Union, Literal
from pydantic import BaseModel, Field, field_validator, model_validator


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
    port: int = Field(..., description="Port number for the agent", ge=1, le=65535)

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

    framework: Literal["langgraph"] = Field(
        default="langgraph",
        description="Agent framework"
    )

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

    @property
    def base_url(self) -> str:
        """Returns the base URL of the agent."""
        return f'http://0.0.0.0:{self.port}'

    @field_validator('port')
    @classmethod
    def validate_port(cls, value):
        """Validate port number is within valid range."""
        if not (1 <= value <= 65535):
            raise ValueError("Port must be between 1 and 65535")
        return value

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

    class Config:
        """Pydantic configuration."""
        extra = "ignore"
        use_enum_values = True
        validate_assignment = True


class ProxyConfig(BaseModel):
    """Configuration for the proxy server."""
    port: int = Field(
        ...,
        description="Port number for the proxy server",
        ge=1,
        le=65535
    )


class AionConfig(BaseModel):
    """Main configuration for Aion system."""

    agents: Dict[str, AgentConfig] = Field(
        default_factory=dict,
        description="Dictionary of agent configurations mapped by agent ID"
    )

    proxy: Optional[ProxyConfig] = Field(
        default=None,
        description="Proxy server configuration"
    )

    @field_validator('agents', mode='before')
    @classmethod
    def validate_agents(cls, value):
        """Validate agents dictionary and convert from various input formats."""
        if value is None:
            return {}

        if isinstance(value, dict):
            # If already a dict, validate each agent config
            result = {}
            for agent_id, agent_config in value.items():
                if isinstance(agent_config, AgentConfig):
                    result[agent_id] = agent_config
                elif isinstance(agent_config, dict):
                    try:
                        result[agent_id] = AgentConfig(**agent_config)
                    except Exception as e:
                        raise ValueError(f"Invalid agent config for '{agent_id}': {e}")
                else:
                    raise ValueError(f"Agent config for '{agent_id}' must be an AgentConfig instance or dict")
            return result

        elif isinstance(value, list):
            # Convert from list format (backward compatibility)
            result = {}
            for i, agent_config in enumerate(value):
                if isinstance(agent_config, AgentConfig):
                    # Use agent name or path as key, fallback to index
                    agent_id = agent_config.name if agent_config.name != "Agent" else f"agent_{i}"
                    result[agent_id] = agent_config
                elif isinstance(agent_config, dict):
                    try:
                        config = AgentConfig(**agent_config)
                        agent_id = config.name if config.name != "Agent" else f"agent_{i}"
                        result[agent_id] = config
                    except Exception as e:
                        raise ValueError(f"Invalid agent config at index {i}: {e}")
                else:
                    raise ValueError(f"Agent config at index {i} must be an AgentConfig instance or dict")
            return result

        else:
            raise ValueError("Agents must be a dictionary or list")

    @model_validator(mode='after')
    def validate_unique_ports(self):
        """Validate that all ports (agents and proxy) are unique."""
        used_ports = []

        # Collect proxy port
        if self.proxy:
            used_ports.append(self.proxy.port)

        # Collect agent ports
        for agent_id, agent in self.agents.items():
            used_ports.append(agent.port)

        # Check for duplicates
        if len(used_ports) != len(set(used_ports)):
            # Find duplicate ports for better error message
            port_counts = {}
            for port in used_ports:
                port_counts[port] = port_counts.get(port, 0) + 1

            duplicates = [port for port, count in port_counts.items() if count > 1]
            raise ValueError(f"Port conflicts detected. The following ports are used multiple times: {duplicates}")

        return self

    @model_validator(mode='after')
    def validate_unique_paths(self):
        """Validate that all agent paths are unique."""
        if self.agents:
            agent_paths = [agent.path for agent in self.agents.values()]
            if len(agent_paths) != len(set(agent_paths)):
                # Find duplicate paths for better error message
                path_counts = {}
                for path in agent_paths:
                    path_counts[path] = path_counts.get(path, 0) + 1

                duplicates = [path for path, count in path_counts.items() if count > 1]
                raise ValueError(
                    f"Agent path conflicts detected. The following paths are used multiple times: {duplicates}")

        return self

    def get_agent(self, agent_id: str) -> Optional[AgentConfig]:
        """Get an agent configuration by ID."""
        return self.agents.get(agent_id)

    def list_agents(self) -> List[str]:
        """Get a list of all agent IDs."""
        return list(self.agents.keys())

    class Config:
        """Pydantic configuration."""
        extra = "ignore"
        use_enum_values = True
        validate_assignment = True

from pydantic import BaseModel, Field, validator, field_validator
from typing import List, Dict, Any, Optional


class AgentCapabilities(BaseModel):
    """Agent capabilities configuration."""
    streaming: bool = Field(default=True, description="Whether agent supports streaming responses")
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
    name: str = Field(default="Agent", description="Human-readable name of the agent")
    description: str = Field(default="", description="Detailed description of the agent")
    version: str = Field(default="1.0.0", description="Agent version")

    # Capabilities and features
    capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities, description="Agent capabilities")
    skills: List[AgentSkill] = Field(default_factory=list, description="List of agent skills")

    # Input/output modes
    input_modes: List[str] = Field(default_factory=lambda: ["text"], description="Supported input modes")
    output_modes: List[str] = Field(default_factory=lambda: ["text"], description="Supported output modes")

    # Additional configuration
    config: Dict[str, Any] = Field(default_factory=dict, description="Additional configuration parameters")

    @field_validator('input_modes', 'output_modes')
    def validate_modes(cls, v):
        """Validate input/output modes."""
        if not v:
            raise ValueError("At least one mode must be specified")

        valid_modes = {"text", "audio", "image", "video", "json"}
        for mode in v:
            if mode not in valid_modes:
                raise ValueError(f"Invalid mode: {mode}. Must be one of {valid_modes}")
        return v

    @field_validator('version')
    def validate_version(cls, v):
        """Validate version format."""
        import re
        if not re.match(r'^\d+\.\d+\.\d+$', v):
            raise ValueError("Version must be in format X.Y.Z")
        return v

    @field_validator('skills')
    def validate_skills(cls, v):
        """Validate skills have unique IDs."""
        if v:
            skill_ids = [skill.id for skill in v]
            if len(skill_ids) != len(set(skill_ids)):
                raise ValueError("Skill IDs must be unique")
        return v

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
            output_modes=['text']
        )

    class Config:
        """Pydantic configuration."""
        extra = "forbid"  # Don't allow extra fields
        use_enum_values = True
        validate_assignment = True

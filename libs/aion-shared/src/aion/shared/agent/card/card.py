from a2a.types import AgentCard
from a2a.types import AgentExtension, AgentCapabilities, AgentSkill
from aion.shared.config import AgentConfig

from aion.shared.settings import app_settings
from aion.shared.types import GetContextParams, GetContextsListParams
from . import collectors


class AionAgentCard(AgentCard):
    """
    Extended AgentCard with configuration management capabilities.

    This class extends the base AgentCard to include automatic configuration
    processing through the AgentCardConfigurationCollector. It standardizes
    configuration data into a consistent format suitable for agent management.

    Attributes:
        configuration (dict[str, dict]): Dictionary containing processed configuration
                                       data where keys are field names and values are
                                       configuration dictionaries with validation rules
                                       and metadata.
    """
    configuration: dict[str, dict] = {}

    @classmethod
    def from_config(
            cls,
            config: AgentConfig,
            base_url: str,
    ) -> "AionAgentCard":
        capabilities = AgentCapabilities(
            streaming=config.capabilities.streaming,
            push_notifications=config.capabilities.pushNotifications,
            extensions=[
                AgentExtension(
                    description="Get Conversation info based on context",
                    params=GetContextParams.model_json_schema(),
                    required=False,
                    uri=f"{app_settings.docs_url}/a2a/extensions/get-context"
                ),
                AgentExtension(
                    description="Get list of available contexts",
                    params=GetContextsListParams.model_json_schema(),
                    required=False,
                    uri=f"{app_settings.docs_url}/a2a/extensions/get-contexts"
                )
            ])

        skills = []
        for skill_config in config.skills:
            skill = AgentSkill(
                id=skill_config.id,
                name=skill_config.name,
                description=skill_config.description,
                tags=skill_config.tags,
                examples=skill_config.examples)
            skills.append(skill)

        return cls(
            name=config.name or "Graph Agent",
            description=config.description or "Agent based on external graph",
            url=base_url,
            version=config.version or "1.0.0",
            default_input_modes=config.input_modes,
            default_output_modes=config.output_modes,
            capabilities=capabilities,
            skills=skills,
            configuration=collectors.AgentCardConfigurationCollector(config.configuration).collect()
        )


__all__ = [
    "AionAgentCard",
]

"""Builder for the A2A AgentCard with Aion extensions and capabilities."""

from a2a.types import (
    AgentExtension,
    AgentCapabilities,
    AgentInterface,
    AgentSkill,
    AgentCard,
)

from aion.core.config import AgentConfig
from aion.core.runtime import aion_a2a_extension_registry


class AionAgentCard:
    """Factory for creating AgentCard with Aion-specific extensions."""

    @classmethod
    def from_config(
            cls,
            config: AgentConfig,
            base_url: str,
    ) -> AgentCard:
        """Build an AgentCard from agent config and the server's base URL."""
        extensions = [
            AgentExtension(uri=ext.uri, description=ext.description, required=False)
            for ext in aion_a2a_extension_registry.get_all()
            if ext.active
        ]
        capabilities = AgentCapabilities(
            streaming=True,
            push_notifications=True,
            extensions=extensions,
        )

        skills = []
        for skill_config in config.skills:
            skill = AgentSkill(
                id=skill_config.id,
                name=skill_config.name,
                description=skill_config.description,
                tags=skill_config.tags,
                examples=skill_config.examples)
            skills.append(skill)

        supported_interfaces = [
            AgentInterface(url=base_url, protocol_binding="JSONRPC", protocol_version="1.0"),
            AgentInterface(url=base_url, protocol_binding="JSONRPC", protocol_version="0.3"),
        ]

        return AgentCard(
            name=config.name or "Graph Agent",
            description=config.description or "Agent based on external graph",
            supported_interfaces=supported_interfaces,
            version=config.version or "1.0.0",
            default_input_modes=config.input_modes,
            default_output_modes=config.output_modes,
            capabilities=capabilities,
            skills=skills
        )


__all__ = [
    "AionAgentCard",
]

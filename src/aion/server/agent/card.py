"""Builder for the A2A AgentCard with Aion extensions and capabilities."""

from a2a.types import (
    AgentExtension,
    AgentCapabilities,
    AgentInterface,
    AgentSkill,
    AgentCard,
    HTTPAuthSecurityScheme,
    SecurityScheme,
)

from aion.core.config import AgentConfig
from aion.core.runtime import aion_a2a_extension_registry
from aion.server.auth import authentication_required

BEARER_SECURITY_SCHEME = "bearer"
"""The name the card gives the bearer token every request to the agent carries."""


class AionAgentCard:
    """Factory for creating AgentCard with Aion-specific extensions."""

    @classmethod
    def from_config(
            cls,
            config: AgentConfig,
            base_url: str,
    ) -> AgentCard:
        """Build an AgentCard from agent config and the server's base URL."""
        # Which extensions belong on a card is the registry's decision, not
        # this builder's: an extension can be supported and callable without
        # being something a standard agent announces (see get_advertised and
        # docs/development/extension-exposure.md).
        extensions = [
            AgentExtension(uri=ext.uri, description=ext.description, required=False)
            for ext in aion_a2a_extension_registry.get_advertised()
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

        card = AgentCard(
            name=config.name or "Graph Agent",
            description=config.description or "Agent based on external graph",
            supported_interfaces=supported_interfaces,
            version=config.version or "1.0.0",
            default_input_modes=config.input_modes,
            default_output_modes=config.output_modes,
            capabilities=capabilities,
            skills=skills,
        )
        if authentication_required():
            cls._require_bearer_token(card)
        return card

    @staticmethod
    def _require_bearer_token(card: AgentCard) -> None:
        """Say on the card that every call needs the platform's bearer token.

        With credentials the server refuses a call without one
        (``AionAuthMiddleware``); the card is where a client learns that before
        its first call is refused. In local mode nothing is asked for, and the
        card says nothing.

        Only the scheme is published, not ``security_requirements``. A bearer
        token has no scopes, so the requirement's scope list is empty, and
        proto3 JSON writes an empty ``StringList`` as ``{}``. The platform's
        card decoder expects a list there and refuses the card, which fails the
        deployment's registration.
        """
        card.security_schemes[BEARER_SECURITY_SCHEME].CopyFrom(
            SecurityScheme(
                http_auth_security_scheme=HTTPAuthSecurityScheme(
                    scheme="Bearer",
                    bearer_format="JWT",
                    description="A token the Aion platform signs for this deployment.",
                )
            )
        )


__all__ = [
    "AionAgentCard",
    "BEARER_SECURITY_SCHEME",
]

from __future__ import annotations

from typing import Any

from a2a.types import AgentCard


class AgentCardMigrator:
    """
    Migrates an A2A AgentCard from v0.3.x to v1.0.

    Takes an ``AgentCard`` pydantic instance (v0.3) and rewrites it to
    conform to the v1.0 schema. The result contains only a JSONRPC binding —
    any other transports from v0.3 additionalInterfaces are dropped.

    Example::

        card_v1 = AgentCardMigrator(card_v03).migrate()
    """

    def __init__(self, card_v03: AgentCard) -> None:
        self._src = card_v03
        self._dst: dict[str, Any] = {}

    def migrate(self) -> dict[str, Any]:
        """Run the full v0.3 → v1.0 migration and return the resulting card dict."""
        self._migrate_identity()
        self._migrate_interfaces()
        self._migrate_capabilities()
        self._migrate_security()
        self._migrate_content_modes()
        self._migrate_skills()
        return self._dst

    def _migrate_identity(self) -> None:
        """Copy name, description, version, url, and any optional identity fields."""
        self._dst["name"] = self._src.name
        self._dst["description"] = self._src.description
        self._dst["version"] = self._src.version or "0.0.0"
        self._dst["url"] = self._src.url or ""

        if self._src.documentation_url is not None:
            self._dst["documentationUrl"] = self._src.documentation_url
        if self._src.icon_url is not None:
            self._dst["iconUrl"] = self._src.icon_url
        if self._src.provider is not None:
            self._dst["provider"] = self._src.provider.model_dump(
                by_alias=True, exclude_none=True
            )

    def _migrate_interfaces(self) -> None:
        """
        Build supportedInterfaces advertising both supported protocol versions.

        Both v1.0 and v0.3 are listed because the server accepts either via the
        A2A-Version request header (v1.0 is handled through the compat layer).
        v0.3 additionalInterfaces from the source card are ignored — only the
        primary url is carried over.
        """
        url = self._src.url or ""
        self._dst["supportedInterfaces"] = [
            {"url": url, "protocolBinding": "JSONRPC", "protocolVersion": "0.3"},
            {"url": url, "protocolBinding": "JSONRPC", "protocolVersion": "1.0"},
        ]

    def _migrate_capabilities(self) -> None:
        """
        Migrate capabilities from v0.3 to v1.0.

        supportsAuthenticatedExtendedCard is mapped to
        capabilities.extendedAgentCard.
        """
        caps = self._src.capabilities
        caps_dst: dict[str, Any] = {}

        if caps.streaming is not None:
            caps_dst["streaming"] = caps.streaming
        if caps.push_notifications is not None:
            caps_dst["pushNotifications"] = caps.push_notifications
        if caps.state_transition_history is not None:
            caps_dst["stateTransitionHistory"] = caps.state_transition_history
        if caps.extensions:
            caps_dst["extensions"] = [
                e.model_dump(by_alias=True, exclude_none=True) for e in caps.extensions
            ]

        if self._src.supports_authenticated_extended_card is not None:
            caps_dst["extendedAgentCard"] = bool(
                self._src.supports_authenticated_extended_card
            )

        self._dst["capabilities"] = caps_dst

    def _migrate_security(self) -> None:
        """
        Carry over v0.3 securitySchemes and security (OpenAPI 3.x style)
        into v1.0 securitySchemes and securityRequirements.
        """
        if self._src.security_schemes:
            self._dst["securitySchemes"] = {
                key: scheme.model_dump(by_alias=True, exclude_none=True)
                for key, scheme in self._src.security_schemes.items()
            }
        if self._src.security:
            self._dst["securityRequirements"] = self._src.security

    def _migrate_content_modes(self) -> None:
        """Copy defaultInputModes and defaultOutputModes, falling back to ["text/plain"]."""
        self._dst["defaultInputModes"] = self._src.default_input_modes or ["text/plain"]
        self._dst["defaultOutputModes"] = self._src.default_output_modes or ["text/plain"]

    def _migrate_skills(self) -> None:
        """Serialize skills to dicts."""
        self._dst["skills"] = [
            s.model_dump(by_alias=True, exclude_none=True) for s in self._src.skills
        ]

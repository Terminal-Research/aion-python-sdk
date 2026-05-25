"""URL path helpers for Aion control-plane ingress surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from aion.core.settings import api_settings

from .models import (
    CapabilityKey,
    CapabilityKind,
    CapabilityReference,
    CapabilitySubject,
    CapabilitySubjectKind,
)


@dataclass(frozen=True)
class AionControlPlanePaths:
    """Build Aion control-plane relative paths and absolute URLs.

    The generic capability methods accept ``CapabilityReference`` values,
    which model URLs as ``subject + kind + key selector``. Existing
    protocol-specific methods are convenience wrappers for code that already
    knows the exact endpoint family.
    """

    base_url: str | None = None
    """Optional Aion API base URL. Defaults to ``AION_API_HOST``."""

    def api_base_url(self) -> str:
        """Return the normalized Aion API base URL.

        Returns:
            The configured API base URL without a trailing slash.
        """
        return (self.base_url or api_settings.http_url).rstrip("/")

    def control_plane_mcp_path(self) -> str:
        """Return the relative path for the global control-plane MCP server.

        Returns:
            The relative HTTP path for the control-plane MCP endpoint.
        """
        return "/mcp"

    def control_plane_mcp_url(self) -> str:
        """Return the absolute URL for the global control-plane MCP server.

        Returns:
            The absolute HTTP URL for the control-plane MCP endpoint.
        """
        return f"{self.api_base_url()}{self.control_plane_mcp_path()}"

    def capability_path(self, reference: CapabilityReference) -> str:
        """Return the relative path for a capability reference.

        Args:
            reference: Capability reference combining optional subject, kind,
                and capability-key selector.

        Returns:
            The relative HTTP path for the referenced capability endpoint.

        Raises:
            ValueError: If the capability kind is not HTTP-addressable or the
                reference does not contain the subject required by that kind.
        """
        if reference.kind == CapabilityKind.MCP_SERVER:
            return self._mcp_path(reference)
        if reference.kind == CapabilityKind.A2A_ENDPOINT:
            return self._a2a_path(reference)
        raise ValueError("event emitter capabilities do not have an HTTP route")

    def capability_url(self, reference: CapabilityReference) -> str:
        """Return the absolute URL for a capability reference.

        Args:
            reference: Capability reference combining optional subject, kind,
                and capability-key selector.

        Returns:
            The absolute HTTP URL for the referenced capability endpoint.
        """
        return f"{self.api_base_url()}{self.capability_path(reference)}"

    def mcp_capability_path(
        self,
        subject: CapabilitySubject,
        capability_key: str,
    ) -> str:
        """Return the relative path for a capability MCP endpoint.

        Args:
            subject: Capability subject addressed by the MCP endpoint.
            capability_key: Capability key exposed as an MCP server.

        Returns:
            The relative HTTP path for the capability MCP endpoint.
        """
        return self.capability_path(
            CapabilityReference.mcp(
                subject,
                key=CapabilityKey.concrete(capability_key),
            )
        )

    def mcp_capability_url(
        self,
        subject: CapabilitySubject,
        capability_key: str,
    ) -> str:
        """Return the absolute URL for a capability MCP endpoint.

        Args:
            subject: Capability subject addressed by the MCP endpoint.
            capability_key: Capability key exposed as an MCP server.

        Returns:
            The absolute HTTP URL for the capability MCP endpoint.
        """
        return (
            f"{self.api_base_url()}"
            f"{self.mcp_capability_path(subject, capability_key)}"
        )

    def a2a_path(self, subject: CapabilitySubject) -> str:
        """Return the relative path for an A2A JSON-RPC endpoint.

        Args:
            subject: Capability subject addressed by the A2A endpoint.

        Returns:
            The relative HTTP path for the A2A endpoint.
        """
        return self.capability_path(CapabilityReference.primary_a2a(subject))

    def a2a_url(self, subject: CapabilitySubject) -> str:
        """Return the absolute URL for an A2A JSON-RPC endpoint.

        Args:
            subject: Capability subject addressed by the A2A endpoint.

        Returns:
            The absolute HTTP URL for the A2A endpoint.
        """
        return f"{self.api_base_url()}{self.a2a_path(subject)}"

    def agent_card_path(self, subject: CapabilitySubject) -> str:
        """Return the relative path for A2A agent-card discovery.

        Args:
            subject: Capability subject addressed by discovery.

        Returns:
            The relative HTTP path for the subject's agent card.
        """
        return f"{self.a2a_path(subject)}/.well-known/agent-card.json"

    def agent_card_url(self, subject: CapabilitySubject) -> str:
        """Return the absolute URL for A2A agent-card discovery.

        Args:
            subject: Capability subject addressed by discovery.

        Returns:
            The absolute HTTP URL for the subject's agent card.
        """
        return f"{self.api_base_url()}{self.agent_card_path(subject)}"

    def subject_path(self, subject: CapabilitySubject) -> str:
        """Return the relative path prefix for a capability subject.

        Args:
            subject: Capability subject to address.

        Returns:
            The relative path prefix for the subject.
        """
        if subject.kind == CapabilitySubjectKind.DISTRIBUTION:
            return f"/distributions/{_path_part(subject.value)}"
        if subject.kind == CapabilitySubjectKind.ENVIRONMENT:
            return f"/environments/{_path_part(subject.value)}"
        if subject.kind == CapabilitySubjectKind.AGENT_IDENTITY:
            return f"/agents/{_path_part(subject.value)}"
        return f"/agents/@{_path_part(subject.value)}"

    def _mcp_path(self, reference: CapabilityReference) -> str:
        if reference.subject is None:
            path = "/mcp"
        else:
            path = f"{self.subject_path(reference.subject)}/mcp"
        if reference.key.is_primary:
            return path
        capability_key = _path_part(reference.key.require_concrete())
        return f"{path}/{capability_key}"

    def _a2a_path(self, reference: CapabilityReference) -> str:
        if reference.subject is None:
            raise ValueError("A2A endpoint references require a subject")
        path = f"{self.subject_path(reference.subject)}/a2a"
        if reference.key.is_primary:
            return path
        capability_key = _path_part(reference.key.require_concrete())
        return f"{path}/{capability_key}"


def _path_part(value: str) -> str:
    return quote(str(value), safe="")


__all__ = ["AionControlPlanePaths"]

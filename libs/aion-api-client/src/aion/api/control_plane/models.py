"""Typed Aion control-plane addressing models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from aion.core.runtime.context.models import AionRuntimeContext
    from aion.api.gql.generated.graphql_client.input_types import (
        CapabilitySubjectGQLInput,
        PrincipalSelectorGQLInput,
    )

AION_PRINCIPAL_SELECTOR_HEADER = "Aion-Principal-Selector"
"""HTTP header used to supply an effective Aion principal selector."""

AION_METATOOLS_MCP_CAPABILITY_KEY = "mcp.aion.metatools"
"""Concrete key for the built-in subjectless Aion metatools MCP server."""


class PrincipalSelectorKind(str, Enum):
    """Supported effective-principal selector kinds."""

    AGENT_ENVIRONMENT = "agent-environment"
    AGENT_IDENTITY = "agent-identity"
    AGENT_AT_NAME = "agent-at-name"


class CapabilitySubjectKind(str, Enum):
    """Supported capability subject address kinds."""

    DISTRIBUTION = "distribution"
    ENVIRONMENT = "environment"
    AGENT_IDENTITY = "agent-identity"
    AGENT_AT_NAME = "agent-at-name"


class CapabilitySubjectSource(str, Enum):
    """Runtime-context source used to derive a capability subject."""

    ACTIVE_ENVIRONMENT = "active-environment"
    INCOMING_DISTRIBUTION = "incoming-distribution"
    PRINCIPAL_IDENTITY = "principal-identity"


class CapabilityKind(str, Enum):
    """Control-plane capability surface kinds.

    The enum values mirror the backend ``Capability.Kind`` values. The route
    segment is exposed separately because HTTP paths use protocol-oriented
    lower-case nouns such as ``mcp`` and ``a2a``.
    """

    MCP_SERVER = "McpServer"
    A2A_ENDPOINT = "A2AEndpoint"
    EVENT_EMITTER = "EventEmitter"

    @property
    def route_segment(self) -> str:
        """Return the HTTP route segment convention for this kind.

        Returns:
            The route segment used by control-plane URL builders.

        Raises:
            ValueError: If the capability kind is not addressable as an HTTP
                ingress endpoint.
        """
        if self == CapabilityKind.MCP_SERVER:
            return "mcp"
        if self == CapabilityKind.A2A_ENDPOINT:
            return "a2a"
        raise ValueError("event emitter capabilities do not have an HTTP route")


class CapabilityKeySelector(str, Enum):
    """Capability key selector mode."""

    PRIMARY = "primary"
    CONCRETE = "concrete"


@dataclass(frozen=True)
class PrincipalSelector:
    """Effective-principal selector for Aion control-plane requests.

    The selector is request metadata, not an authorization grant. Aion API
    routes validate that the authenticated caller may act as the selected
    principal.
    """

    kind: PrincipalSelectorKind
    value: str

    @classmethod
    def agent_environment(cls, agent_environment_id: str) -> Self:
        """Create a selector for an agent environment principal.

        Args:
            agent_environment_id: Agent environment identifier.

        Returns:
            A principal selector for the environment.
        """
        return cls(
            PrincipalSelectorKind.AGENT_ENVIRONMENT,
            _require_value("agent_environment_id", agent_environment_id),
        )

    @classmethod
    def agent_identity(cls, agent_identity_id: str) -> Self:
        """Create a selector for an agent identity principal.

        Args:
            agent_identity_id: Agent identity identifier.

        Returns:
            A principal selector for the identity.
        """
        return cls(
            PrincipalSelectorKind.AGENT_IDENTITY,
            _require_value("agent_identity_id", agent_identity_id),
        )

    @classmethod
    def agent_at_name(cls, agent_at_name: str) -> Self:
        """Create a selector for an agent principal addressed by ``@name``.

        Args:
            agent_at_name: Agent handle with or without the leading ``@``.

        Returns:
            A principal selector for the handle.
        """
        return cls(
            PrincipalSelectorKind.AGENT_AT_NAME,
            _normalize_at_name(agent_at_name),
        )

    @classmethod
    def from_header_value(cls, raw: str) -> Self:
        """Parse an ``Aion-Principal-Selector`` header value.

        Args:
            raw: Header value using ``<kind>:<value>`` syntax.

        Returns:
            The parsed principal selector.

        Raises:
            ValueError: If the header value is not valid.
        """
        kind, separator, value = raw.strip().partition(":")
        if not separator:
            raise ValueError(
                f"{AION_PRINCIPAL_SELECTOR_HEADER} must use "
                "'<kind>:<value>' syntax"
            )

        normalized_kind = kind.strip().lower()
        if normalized_kind == PrincipalSelectorKind.AGENT_ENVIRONMENT.value:
            return cls.agent_environment(value)
        if normalized_kind == PrincipalSelectorKind.AGENT_IDENTITY.value:
            return cls.agent_identity(value)
        if normalized_kind == PrincipalSelectorKind.AGENT_AT_NAME.value:
            return cls.agent_at_name(value)
        raise ValueError(
            f"Unknown {AION_PRINCIPAL_SELECTOR_HEADER} selector kind "
            f"'{normalized_kind}'"
        )

    @classmethod
    def from_runtime_context(
        cls,
        context: AionRuntimeContext | None,
    ) -> Self | None:
        """Create a selector from the active runtime context, if present.

        Runtime contexts derive the selector from the environment daemon
        identity when available, and otherwise fall back to the active
        environment. This scopes outgoing control-plane calls, such as MCP or
        A2A access, to the principal used for access control.

        Args:
            context: Current Aion runtime context.

        Returns:
            A principal selector, or ``None`` when the context has no runtime
            principal metadata.
        """
        if context is None:
            return None
        raw_selector = context.get_principal_selector()
        if raw_selector is None:
            return None
        return cls.from_header_value(raw_selector)

    def to_header_value(self) -> str:
        """Return the HTTP header value for this selector.

        Returns:
            A string using the backend-supported ``<kind>:<value>`` format.
        """
        return f"{self.kind.value}:{self.value}"

    def to_headers(self) -> dict[str, str]:
        """Return HTTP headers for this principal selector.

        Returns:
            A header mapping containing ``Aion-Principal-Selector``.
        """
        return {AION_PRINCIPAL_SELECTOR_HEADER: self.to_header_value()}

    def to_gql_input(self) -> PrincipalSelectorGQLInput:
        """Return the generated GraphQL input for this selector.

        Returns:
            A ``PrincipalSelectorGQLInput`` selecting exactly one principal.
        """
        PrincipalSelectorGQLInput = _principal_selector_gql_input_type()
        if self.kind == PrincipalSelectorKind.AGENT_ENVIRONMENT:
            return PrincipalSelectorGQLInput(agent_environment_id=self.value)
        if self.kind == PrincipalSelectorKind.AGENT_IDENTITY:
            return PrincipalSelectorGQLInput(agent_identity_id=self.value)
        return PrincipalSelectorGQLInput(agent_at_name=self.value)


@dataclass(frozen=True)
class CapabilitySubject:
    """Capability-addressed control-plane subject.

    Capability subjects identify the resource or agent surface being addressed.
    They are intentionally separate from the effective principal selector.
    """

    kind: CapabilitySubjectKind
    value: str

    @classmethod
    def distribution(cls, distribution_id: str) -> Self:
        """Create a subject for a distribution.

        Args:
            distribution_id: Distribution identifier.

        Returns:
            A capability subject for the distribution.
        """
        return cls(
            CapabilitySubjectKind.DISTRIBUTION,
            _require_value("distribution_id", distribution_id),
        )

    @classmethod
    def environment(cls, agent_environment_id: str) -> Self:
        """Create a subject for an agent environment.

        Args:
            agent_environment_id: Agent environment identifier.

        Returns:
            A capability subject for the environment.
        """
        return cls(
            CapabilitySubjectKind.ENVIRONMENT,
            _require_value("agent_environment_id", agent_environment_id),
        )

    @classmethod
    def agent_environment(cls, agent_environment_id: str) -> Self:
        """Create a subject for an agent environment.

        Args:
            agent_environment_id: Agent environment identifier.

        Returns:
            A capability subject for the environment.
        """
        return cls.environment(agent_environment_id)

    @classmethod
    def agent_identity(cls, agent_identity_id: str) -> Self:
        """Create a subject for an agent identity.

        Args:
            agent_identity_id: Agent identity identifier.

        Returns:
            A capability subject for the identity.
        """
        return cls(
            CapabilitySubjectKind.AGENT_IDENTITY,
            _require_value("agent_identity_id", agent_identity_id),
        )

    @classmethod
    def agent_at_name(cls, agent_at_name: str) -> Self:
        """Create a subject for an agent addressed by ``@name``.

        Args:
            agent_at_name: Agent handle with or without the leading ``@``.

        Returns:
            A capability subject for the handle.
        """
        return cls(
            CapabilitySubjectKind.AGENT_AT_NAME,
            _normalize_at_name(agent_at_name),
        )

    @classmethod
    def from_runtime_context(
        cls,
        context: AionRuntimeContext | None,
        *,
        source: CapabilitySubjectSource = CapabilitySubjectSource.ACTIVE_ENVIRONMENT,
    ) -> Self | None:
        """Create a capability subject from a runtime context, if possible.

        Args:
            context: Current Aion runtime context.
            source: Runtime-context entity to address. The default is the
                active agent environment because that is the most common
                direct daemon target. Use ``INCOMING_DISTRIBUTION`` when an
                agent wants tools for the distribution that delivered the
                request.

        Returns:
            A capability subject derived from ``source``, or ``None`` when the
            requested runtime entity is unavailable.
        """
        if context is None:
            return None
        if source == CapabilitySubjectSource.ACTIVE_ENVIRONMENT:
            environment = context.get_environment()
            if environment is None:
                return None
            return cls.environment(environment.id)
        if source == CapabilitySubjectSource.INCOMING_DISTRIBUTION:
            distribution = context.get_distribution()
            if distribution is None:
                return None
            return cls.distribution(distribution.id)
        identity = context.get_principal_identity()
        if identity is None:
            return None
        return cls.agent_identity(identity.id)

    def to_gql_input(self) -> CapabilitySubjectGQLInput:
        """Return the generated GraphQL input for this subject.

        Returns:
            A ``CapabilitySubjectGQLInput`` selecting exactly one subject.
        """
        CapabilitySubjectGQLInput = _capability_subject_gql_input_type()
        if self.kind == CapabilitySubjectKind.DISTRIBUTION:
            return CapabilitySubjectGQLInput(distribution_id=self.value)
        if self.kind == CapabilitySubjectKind.ENVIRONMENT:
            return CapabilitySubjectGQLInput(agent_environment_id=self.value)
        if self.kind == CapabilitySubjectKind.AGENT_IDENTITY:
            return CapabilitySubjectGQLInput(agent_identity_id=self.value)
        return CapabilitySubjectGQLInput(agent_at_name=self.value)

    @property
    def server_name_fragment(self) -> str:
        """Return a stable name fragment for framework MCP clients.

        Returns:
            A lower-risk identifier fragment derived from the subject kind and
            value.
        """
        value = self.value.replace("-", "_").replace("@", "").replace(".", "_")
        return f"{self.kind.value.replace('-', '_')}_{value}"


@dataclass(frozen=True)
class CapabilityKey:
    """Selector for a capability key.

    A primary selector is intentionally not encoded as the literal string
    ``"primary"``. It means "resolve the primary enabled capability of this
    kind for the addressed subject." Concrete keys identify one exact
    behavior-declared capability such as ``mcp.twitter.distribution``.
    """

    selector: CapabilityKeySelector
    value: str | None = None

    @classmethod
    def primary(cls) -> Self:
        """Create a primary capability-key selector.

        Returns:
            A selector that asks the control plane to resolve the primary
            capability for a kind and subject.
        """
        return cls(CapabilityKeySelector.PRIMARY)

    @classmethod
    def concrete(cls, value: str) -> Self:
        """Create a selector for one concrete capability key.

        Args:
            value: Behavior-declared capability key.

        Returns:
            A concrete capability-key selector.
        """
        return cls(
            CapabilityKeySelector.CONCRETE,
            _require_value("capability_key", value),
        )

    @property
    def is_primary(self) -> bool:
        """Return whether this selector addresses the primary capability."""
        return self.selector == CapabilityKeySelector.PRIMARY

    @property
    def is_concrete(self) -> bool:
        """Return whether this selector addresses a concrete capability key."""
        return self.selector == CapabilityKeySelector.CONCRETE

    def require_concrete(self) -> str:
        """Return the concrete key or raise if this is a primary selector.

        Returns:
            The concrete capability key.

        Raises:
            ValueError: If this selector is primary.
        """
        if self.value is None:
            raise ValueError("primary capability selector has no concrete key")
        return self.value

    @property
    def server_name_fragment(self) -> str:
        """Return a stable name fragment for framework MCP clients."""
        if self.is_primary:
            return "primary"
        return _name_fragment(self.require_concrete())


@dataclass(frozen=True)
class CapabilityReference:
    """SDK-level reference to a control-plane capability surface.

    A reference combines the optional addressed subject, capability kind, and
    key selector. This is an SDK addressing model, not proof that a concrete
    route or capability exists on the current server. Subjectless keyed MCP
    references address system-owned capabilities, such as
    ``/mcp/capabilities/mcp.aion.metatools``. Subjectless primary references
    can be represented for low-level route-shape tests, but current system
    endpoints are expected to be keyed. Subject-qualified references address
    runtime capabilities exposed by distributions, environments, or agent
    identities.
    """

    kind: CapabilityKind
    subject: CapabilitySubject | None = None
    key: CapabilityKey = CapabilityKey.primary()

    @classmethod
    def mcp(
        cls,
        subject: CapabilitySubject | None = None,
        *,
        key: CapabilityKey | str | None = None,
    ) -> Self:
        """Create an MCP server capability reference.

        Args:
            subject: Optional subject exposing the MCP capability. ``None``
                addresses an application-owned MCP capability.
            key: Concrete capability key or ``CapabilityKey.primary()``. When
                omitted, the reference selects the primary MCP capability.

        Returns:
            An MCP capability reference.
        """
        return cls(
            kind=CapabilityKind.MCP_SERVER,
            subject=subject,
            key=_capability_key(key),
        )

    @classmethod
    def primary_mcp(cls, subject: CapabilitySubject) -> Self:
        """Create a subject-qualified primary MCP capability reference.

        Args:
            subject: Subject whose primary MCP capability should be resolved.

        Returns:
            A primary MCP capability reference for ``subject``.
        """
        return cls.mcp(subject, key=CapabilityKey.primary())

    @classmethod
    def global_mcp(
        cls,
        *,
        key: CapabilityKey | str | None = None,
    ) -> Self:
        """Create a reference to the application metatools MCP server.

        Args:
            key: Optional concrete system MCP key. Omitted values select the
                built-in metatools MCP key. Use ``CapabilityReference.mcp`` with
                ``CapabilityKey.primary()`` only when intentionally addressing
                the low-level unkeyed route shape.

        Returns:
            The subjectless MCP capability reference.
        """
        selected_key = AION_METATOOLS_MCP_CAPABILITY_KEY if key is None else key
        return cls.mcp(None, key=selected_key)

    @classmethod
    def a2a(
        cls,
        subject: CapabilitySubject | None = None,
        *,
        key: CapabilityKey | str | None = None,
    ) -> Self:
        """Create an A2A endpoint capability reference.

        Args:
            subject: Optional subject exposing the A2A endpoint. ``None``
                addresses a subjectless system route shape.
            key: Concrete endpoint key or ``CapabilityKey.primary()``. Concrete
                keys are routed under ``/a2a/capabilities/{key}``.

        Returns:
            An A2A endpoint capability reference.
        """
        return cls(
            kind=CapabilityKind.A2A_ENDPOINT,
            subject=subject,
            key=_capability_key(key),
        )

    @classmethod
    def primary_a2a(cls, subject: CapabilitySubject) -> Self:
        """Create a subject-qualified primary A2A endpoint reference.

        Args:
            subject: Subject whose primary A2A endpoint should be resolved.

        Returns:
            A primary A2A endpoint reference for ``subject``.
        """
        return cls.a2a(subject, key=CapabilityKey.primary())

    @classmethod
    def from_runtime_context(
        cls,
        context: AionRuntimeContext | None,
        *,
        kind: CapabilityKind = CapabilityKind.MCP_SERVER,
        source: CapabilitySubjectSource = CapabilitySubjectSource.ACTIVE_ENVIRONMENT,
        key: CapabilityKey | str | None = None,
    ) -> Self | None:
        """Create a capability reference from a runtime context.

        Args:
            context: Current Aion runtime context.
            kind: Capability kind to address.
            source: Runtime-context entity to address as the subject.
            key: Capability key selector. Omitted values select primary.

        Returns:
            A capability reference, or ``None`` when the selected subject is not
            present in the runtime context.
        """
        subject = CapabilitySubject.from_runtime_context(context, source=source)
        if subject is None:
            return None
        return cls(kind=kind, subject=subject, key=_capability_key(key))

    @property
    def server_name_fragment(self) -> str:
        """Return a stable framework-client server-name fragment."""
        subject = (
            "global"
            if self.subject is None
            else self.subject.server_name_fragment
        )
        return (
            f"{subject}_{self.kind.route_segment}_"
            f"{self.key.server_name_fragment}"
        )


@dataclass(frozen=True)
class RuntimeCapabilityReference:
    """Template for resolving a capability reference from runtime context.

    Framework integrations use this type when the desired subject is not known
    until an Aion request arrives. For example, an agent can ask for the
    primary MCP server attached to the incoming distribution without knowing
    the distribution id at graph or ADK agent construction time.
    """

    kind: CapabilityKind = CapabilityKind.MCP_SERVER
    source: CapabilitySubjectSource = CapabilitySubjectSource.ACTIVE_ENVIRONMENT
    key: CapabilityKey = CapabilityKey.primary()

    @classmethod
    def mcp(
        cls,
        *,
        source: CapabilitySubjectSource = CapabilitySubjectSource.ACTIVE_ENVIRONMENT,
        key: CapabilityKey | str | None = None,
    ) -> Self:
        """Create a runtime-resolved MCP capability reference template.

        Args:
            source: Runtime-context entity that supplies the capability
                subject.
            key: Concrete capability key or ``CapabilityKey.primary()``. When
                omitted, the reference selects the primary MCP capability.

        Returns:
            A runtime-resolved MCP reference template.
        """
        return cls(
            kind=CapabilityKind.MCP_SERVER,
            source=source,
            key=_capability_key(key),
        )

    @classmethod
    def primary_mcp(
        cls,
        source: CapabilitySubjectSource = CapabilitySubjectSource.ACTIVE_ENVIRONMENT,
    ) -> Self:
        """Create a runtime-resolved primary MCP reference template.

        Args:
            source: Runtime-context entity that supplies the capability
                subject.

        Returns:
            A template for resolving that subject's primary MCP server.
        """
        return cls.mcp(source=source, key=CapabilityKey.primary())

    def resolve(
        self,
        context: AionRuntimeContext | None,
    ) -> CapabilityReference | None:
        """Resolve this template against a runtime context.

        Args:
            context: Current Aion runtime context.

        Returns:
            A concrete SDK capability reference, or ``None`` when the selected
            runtime subject is unavailable.
        """
        return CapabilityReference.from_runtime_context(
            context,
            kind=self.kind,
            source=self.source,
            key=self.key,
        )


def _capability_key(key: CapabilityKey | str | None) -> CapabilityKey:
    if key is None:
        return CapabilityKey.primary()
    if isinstance(key, CapabilityKey):
        return key
    return CapabilityKey.concrete(key)


def _principal_selector_gql_input_type() -> type[PrincipalSelectorGQLInput]:
    from aion.api.gql.generated.graphql_client.input_types import (
        PrincipalSelectorGQLInput,
    )

    return PrincipalSelectorGQLInput


def _capability_subject_gql_input_type() -> type[CapabilitySubjectGQLInput]:
    from aion.api.gql.generated.graphql_client.input_types import (
        CapabilitySubjectGQLInput,
    )

    return CapabilitySubjectGQLInput


def _require_value(field_name: str, value: Any) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _normalize_at_name(value: str) -> str:
    normalized = _require_value("agent_at_name", value)
    if normalized.startswith("@"):
        normalized = normalized[1:]
    if not normalized:
        raise ValueError("agent_at_name must not be empty")
    return normalized


def _name_fragment(value: str) -> str:
    return (
        value.replace("-", "_")
        .replace("@", "")
        .replace(".", "_")
        .replace("/", "_")
    )


__all__ = [
    "AION_METATOOLS_MCP_CAPABILITY_KEY",
    "AION_PRINCIPAL_SELECTOR_HEADER",
    "CapabilityKey",
    "CapabilityKeySelector",
    "CapabilityKind",
    "CapabilityReference",
    "CapabilitySubject",
    "CapabilitySubjectKind",
    "CapabilitySubjectSource",
    "PrincipalSelector",
    "PrincipalSelectorKind",
    "RuntimeCapabilityReference",
]

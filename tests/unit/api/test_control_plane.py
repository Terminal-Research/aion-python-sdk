"""Tests for typed Aion control-plane addressing utilities."""

from __future__ import annotations

import pytest

from aion.api.control_plane import (
    AION_METATOOLS_MCP_CAPABILITY_KEY,
    AION_PRINCIPAL_SELECTOR_HEADER,
    AionControlPlanePaths,
    CapabilityKey,
    CapabilityKind,
    CapabilityReference,
    CapabilitySubject,
    CapabilitySubjectSource,
    PrincipalSelector,
    RuntimeCapabilityReference,
)
from aion.api.gql.generated.graphql_client import (
    CapabilitySubjectGQLInput,
)


class FakeEnvironment:
    """Minimal environment used to exercise runtime-context coercion."""

    id = "env-123"


class FakeDistribution:
    """Minimal distribution used to exercise subject-source selection."""

    id = "dist-123"


class FakePrincipalIdentity:
    """Minimal principal identity used to exercise subject-source selection."""

    id = "agent-123"


class FakeRuntimeContext:
    """Minimal runtime context used to exercise SDK helpers."""

    def get_principal_selector(self) -> str:
        """Return the fake environment selector."""
        return "aion://agent/environment/env-123"

    def get_environment(self) -> FakeEnvironment:
        """Return the fake environment."""
        return FakeEnvironment()

    def get_distribution(self) -> FakeDistribution:
        """Return the fake distribution."""
        return FakeDistribution()

    def get_principal_identity(self) -> FakePrincipalIdentity:
        """Return the fake principal identity."""
        return FakePrincipalIdentity()


class FakeDaemonRuntimeContext(FakeRuntimeContext):
    """Runtime context with a daemon identity selector."""

    def get_principal_selector(self) -> str:
        """Return the fake daemon identity selector."""
        return "aion://agent/identity/daemon-123"


def test_principal_selector_builds_header_and_gql_value() -> None:
    """Verify principal selectors serialize for HTTP and GraphQL."""
    selector = PrincipalSelector.agent_environment("env-123")

    assert selector.to_header_value() == "aion://agent/environment/env-123"
    assert str(selector) == "aion://agent/environment/env-123"
    assert selector.to_headers() == {
        AION_PRINCIPAL_SELECTOR_HEADER: "aion://agent/environment/env-123"
    }
    assert selector.to_gql_value() == "aion://agent/environment/env-123"


def test_principal_selector_normalizes_at_name() -> None:
    """Verify at-name selectors accept the leading @ sigil."""
    selector = PrincipalSelector.from_header_value(
        "aion://agent/identity/name/@daemon"
    )

    assert selector == PrincipalSelector.agent_at_name("daemon")
    assert selector.to_header_value() == "aion://agent/identity/name/daemon"


def test_principal_selector_rejects_previous_header_form() -> None:
    """Verify the previous selector syntax is no longer accepted."""
    with pytest.raises(ValueError, match="must use an abstract Aion resource URI"):
        PrincipalSelector.from_header_value("project:abc")


def test_principal_selector_rejects_non_principal_resource_uri() -> None:
    """Verify resource URIs must address supported principal resources."""
    with pytest.raises(ValueError, match="cannot select project resources"):
        PrincipalSelector.from_header_value("aion://project/project-1")


class FakeMissingSelectorRuntimeContext(FakeRuntimeContext):
    """Runtime context without principal selector metadata."""

    def get_principal_selector(self) -> None:
        """Return no principal selector."""
        return None


def test_principal_selector_from_runtime_context_prefers_daemon_identity() -> None:
    """Verify runtime selectors can select daemon identity principals."""
    selector = PrincipalSelector.from_runtime_context(FakeDaemonRuntimeContext())

    assert selector == PrincipalSelector.agent_identity("daemon-123")


def test_principal_selector_from_runtime_context_uses_runtime_selector() -> None:
    """Verify runtime selector uses the context-provided selector directly."""
    selector = PrincipalSelector.from_runtime_context(FakeRuntimeContext())

    assert selector == PrincipalSelector.agent_environment("env-123")


def test_principal_selector_from_runtime_context_returns_none() -> None:
    """Verify missing runtime selector metadata remains absent."""
    selector = PrincipalSelector.from_runtime_context(
        FakeMissingSelectorRuntimeContext()
    )

    assert selector is None


def test_capability_subject_builds_gql_input() -> None:
    """Verify capability subjects serialize for GraphQL."""
    subject = CapabilitySubject.environment("env-123")

    assert subject.to_gql_input() == CapabilitySubjectGQLInput(
        agent_environment_id="env-123"
    )


def test_capability_subject_from_runtime_context_uses_environment() -> None:
    """Verify runtime contexts address capability MCP by environment."""
    subject = CapabilitySubject.from_runtime_context(FakeRuntimeContext())

    assert subject == CapabilitySubject.environment("env-123")


def test_capability_subject_can_select_runtime_distribution() -> None:
    """Verify runtime contexts can address the incoming distribution."""
    subject = CapabilitySubject.from_runtime_context(
        FakeRuntimeContext(),
        source=CapabilitySubjectSource.INCOMING_DISTRIBUTION,
    )

    assert subject == CapabilitySubject.distribution("dist-123")


def test_runtime_capability_reference_resolves_from_runtime_context() -> None:
    """Verify runtime templates defer subject selection until invocation."""
    reference = RuntimeCapabilityReference.primary_mcp(
        CapabilitySubjectSource.INCOMING_DISTRIBUTION
    ).resolve(FakeRuntimeContext())

    assert reference == CapabilityReference.primary_mcp(
        CapabilitySubject.distribution("dist-123")
    )


def test_capability_key_primary_is_not_the_literal_primary_key() -> None:
    """Verify primary selection stays distinct from concrete key strings."""
    primary = CapabilityKey.primary()
    concrete = CapabilityKey.concrete("primary")

    assert primary.is_primary
    assert concrete.is_concrete
    assert concrete.require_concrete() == "primary"
    with pytest.raises(ValueError, match="primary capability selector"):
        primary.require_concrete()


def test_control_plane_paths_cover_mcp_a2a_and_agent_cards() -> None:
    """Verify control-plane paths match backend route shapes."""
    paths = AionControlPlanePaths(base_url="https://api.example.test/")

    assert paths.control_plane_mcp_url() == (
        "https://api.example.test/mcp/capabilities/mcp.aion.metatools"
    )
    assert paths.mcp_capability_url(
        CapabilitySubject.environment("env 123"),
        "custom/key",
    ) == (
        "https://api.example.test/environments/env%20123/"
        "mcp/capabilities/custom%2Fkey"
    )
    assert paths.a2a_path(
        CapabilitySubject.distribution("dist-123")
    ) == "/distributions/dist-123/a2a"
    assert paths.agent_card_url(CapabilitySubject.agent_at_name("@daemon")) == (
        "https://api.example.test/agents/@daemon/a2a/.well-known/agent-card.json"
    )


def test_control_plane_paths_accept_capability_references() -> None:
    """Verify generic paths are built from subject, kind, and key selector."""
    paths = AionControlPlanePaths(base_url="https://api.example.test/")

    assert paths.capability_url(CapabilityReference.global_mcp()) == (
        "https://api.example.test/mcp/capabilities/mcp.aion.metatools"
    )
    assert paths.capability_path(
        CapabilityReference.global_mcp(
            key=AION_METATOOLS_MCP_CAPABILITY_KEY
        )
    ) == "/mcp/capabilities/mcp.aion.metatools"
    assert paths.capability_path(
        CapabilityReference.primary_mcp(
            CapabilitySubject.distribution("dist-123")
        )
    ) == "/distributions/dist-123/mcp"
    assert paths.capability_path(
        CapabilityReference.mcp(
            CapabilitySubject.distribution("dist-123"),
            key="custom/key",
        )
    ) == "/distributions/dist-123/mcp/capabilities/custom%2Fkey"
    assert paths.capability_path(
        CapabilityReference.primary_a2a(
            CapabilitySubject.environment("env-123")
        )
    ) == "/environments/env-123/a2a"
    assert paths.capability_path(
        CapabilityReference.a2a(
            CapabilitySubject.environment("env-123"),
            key="a2a.daemon",
        )
    ) == "/environments/env-123/a2a/capabilities/a2a.daemon"
    assert paths.capability_path(
        CapabilityReference.a2a(key="a2a.system")
    ) == "/a2a/capabilities/a2a.system"


def test_control_plane_paths_handle_unaddressable_references() -> None:
    """Verify unsupported kinds fail while subjectless A2A remains addressable."""
    paths = AionControlPlanePaths(base_url="https://api.example.test/")

    with pytest.raises(ValueError, match="event emitter"):
        paths.capability_path(
            CapabilityReference(
                kind=CapabilityKind.EVENT_EMITTER,
                subject=CapabilitySubject.environment("env-123"),
            )
        )

    assert (
        paths.capability_path(
            CapabilityReference(
                kind=CapabilityKind.A2A_ENDPOINT,
                subject=None,
            )
        )
        == "/a2a"
    )

"""Tests for runtime context event and distribution payload handling."""

import pytest
from copy import deepcopy
from unittest.mock import MagicMock, patch
from a2a.types import Message, Part, Role
from google.protobuf.json_format import ParseDict
from google.protobuf.struct_pb2 import Struct

from aion.core.constants.a2a import (
    DAEMON_EXTENSION_URI_V1,
    DISTRIBUTION_EXTENSION_URI_V1,
    EVENT_EXTENSION_URI_V1,
    MESSAGE_EVENT_PAYLOAD_SCHEMA_V1,
    MESSAGE_EVENT_TYPE_V1,
    MESSAGING_EXTENSION_URI_V1,
    REACTION_EVENT_TYPE_V1,
    REACTION_EVENT_PAYLOAD_SCHEMA_V1,
    TRACEABILITY_EXTENSION_URI_V1,
)
from aion.core.runtime.context.builder import AionRuntimeContextBuilder
from aion.core.runtime.context.extensions import (
    AionRuntimeExtensions,
    ExtensionActivationError,
    ExtensionDescriptor,
    aion_a2a_extension_registry,
)
from aion.core.runtime.context.models import (
    AionExtensions,
    AionRuntimeContext,
    EventKind,
)
from aion.core.runtime.context.utils import extract_event
from aion.core.a2a.extensions.distribution import (
    PrincipalIdentity,
    Behavior,
    DistributionExtensionV1,
    Distribution,
    Environment,
    ServiceIdentity,
)
from aion.core.a2a.models import A2AInbox


def _make_inbox_with_event(
    event_type: str,
    payload_schema: str,
    payload_fields: dict,
    source: str = "test://src",
    event_id: str = "ev-1",
) -> A2AInbox:
    """Build a minimal A2AInbox carrying a typed event."""
    msg = Message(message_id="msg-1", role=Role.ROLE_USER)
    msg.metadata.get_or_create_struct(EVENT_EXTENSION_URI_V1).update({
        "type": event_type,
        "source": source,
        "id": event_id,
    })

    part = Part()
    part.metadata.get_or_create_struct(EVENT_EXTENSION_URI_V1).update({
        "schema": payload_schema,
    })
    s = Struct()
    ParseDict(payload_fields, s)
    part.data.struct_value.CopyFrom(s)
    msg.parts.append(part)

    return A2AInbox(message=msg, metadata={})


def _make_distribution_ext(
    agent_id: str = "agent-1",
    behavior_key: str = "main",
    version_id: str = "v-1",
    env_id: str = "env-1",
    env_name: str = "prod",
    daemon_agent_identity_id: str | None = None,
    config_vars: dict | None = None,
    include_principal: bool = True,
    include_service: bool = False,
) -> DistributionExtensionV1:
    identities = []
    if include_principal:
        identities.append(
            PrincipalIdentity(
                kind="principal",
                id=agent_id,
                network_type="aion",
                organization_id="org-1",
                display_name="Bot",
                user_name="bot",
            )
        )
    if include_service:
        identities.append(
            ServiceIdentity(
                kind="service",
                id="svc-1",
                network_type="slack",
                organization_id="org-1",
                display_name="Slack App",
            )
        )

    return DistributionExtensionV1(
        distribution=Distribution(
            id="dist-1",
            endpoint_type="slack",
            url="https://slack.com",
            identities=identities,
        ),
        behavior=Behavior(id="beh-1", behavior_key=behavior_key, version_id=version_id),
        environment=Environment(
            id=env_id,
            name=env_name,
            deployment_id="dep-1",
            configuration_variables=config_vars or {},
            daemon_agent_identity_id=daemon_agent_identity_id,
        ),
    )


class TestExtractEventErrors:
    def test_missing_message_raises(self):
        """Verify that missing message raises."""
        inbox = A2AInbox(message=None, metadata={})
        with pytest.raises(ValueError, match="message is missing"):
            extract_event(inbox)

    def test_missing_event_extension_raises(self):
        """Verify that missing event extension raises."""
        msg = Message(message_id="m1", role=Role.ROLE_USER)
        inbox = A2AInbox(message=msg, metadata={})
        with pytest.raises(ValueError, match="Missing event metadata"):
            extract_event(inbox)

    def test_unrecognized_event_type_raises(self):
        """Verify that unrecognized event type raises."""
        msg = Message(message_id="m1", role=Role.ROLE_USER)
        msg.metadata.get_or_create_struct(EVENT_EXTENSION_URI_V1).update({
            "type": "unknown.event.type",
            "source": "test",
            "id": "ev-1",
        })
        inbox = A2AInbox(message=msg, metadata={})
        with pytest.raises(ValueError, match="Unrecognized event type"):
            extract_event(inbox)

    def test_known_type_but_no_matching_part_raises(self):
        """Verify that known type but no matching part raises."""
        msg = Message(message_id="m1", role=Role.ROLE_USER)
        msg.metadata.get_or_create_struct(EVENT_EXTENSION_URI_V1).update({
            "type": MESSAGE_EVENT_TYPE_V1,
            "source": "test",
            "id": "ev-1",
        })
        # No parts attached
        inbox = A2AInbox(message=msg, metadata={})
        with pytest.raises(ValueError, match="No recognized payload"):
            extract_event(inbox)


class TestExtractEventSuccess:
    def test_message_event_parsed(self):
        """Verify that message event parsed."""
        inbox = _make_inbox_with_event(
            event_type=MESSAGE_EVENT_TYPE_V1,
            payload_schema=MESSAGE_EVENT_PAYLOAD_SCHEMA_V1,
            payload_fields={
                "userId": "u-1",
                "contextId": "c-1",
                "messageId": "m-ext-1",
                "trajectory": "direct-message",
            },
            source="slack://workspace",
            event_id="ev-42",
        )
        event = extract_event(inbox)

        assert event.kind == EventKind.MESSAGE
        assert event.id == "ev-42"
        assert event.source == "slack://workspace"
        assert event.payload.user_id == "u-1"
        assert event.payload.trajectory == "direct-message"

    def test_reaction_event_parsed(self):
        """Verify that reaction event parsed."""
        inbox = _make_inbox_with_event(
            event_type=REACTION_EVENT_TYPE_V1,
            payload_schema=REACTION_EVENT_PAYLOAD_SCHEMA_V1,
            payload_fields={
                "userId": "u-2",
                "contextId": "c-2",
                "messageId": "m-2",
                "reactionKey": "thumbsup",
                "action": "added",
            },
        )
        event = extract_event(inbox)

        assert event.kind == EventKind.REACTION
        assert event.payload.reaction_key == "thumbsup"
        assert event.payload.action == "added"


class TestAionRuntimeContextDistributionPayload:
    def test_distribution_extension_payload_stored(self):
        """Verify that the raw distribution extension payload is stored."""
        dist = _make_distribution_ext(agent_id="agent-abc", behavior_key="main", version_id="v-3")
        ctx = AionRuntimeContext(distribution_extension_payload=dist)

        assert ctx.distribution_extension_payload is dist
        assert ctx.get_distribution().id == "dist-1"

    def test_camel_case_distribution_payload_keyword_rejected(self):
        """Verify that the old camelCase keyword is not silently captured."""
        dist = _make_distribution_ext(agent_id="agent-abc", behavior_key="main", version_id="v-3")

        with pytest.raises(TypeError, match="distribution_extension_payload"):
            AionRuntimeContext(distributionExtensionPayload=dist)

    def test_principal_identity_extracted(self):
        """Verify that principal identity is read from the distribution payload."""
        dist = _make_distribution_ext(agent_id="agent-abc", behavior_key="main", version_id="v-3")
        ctx = AionRuntimeContext(distribution_extension_payload=dist)
        identity = ctx.get_principal_identity()

        assert identity.id == "agent-abc"
        assert identity.display_name == "Bot"
        assert identity.user_name == "bot"
        assert identity.network_type == "aion"

    def test_service_identity_extracted(self):
        """Verify that service identity is read from the distribution payload."""
        dist = _make_distribution_ext(include_service=True)
        ctx = AionRuntimeContext(distribution_extension_payload=dist)

        service_identity = ctx.get_service_identity()

        assert service_identity.id == "svc-1"
        assert service_identity.network_type == "slack"

    def test_behavior_returned(self):
        """Verify that behavior is returned from the distribution payload."""
        dist = _make_distribution_ext(behavior_key="my-behavior", version_id="2.0.0")
        ctx = AionRuntimeContext(distribution_extension_payload=dist)

        assert ctx.get_behavior().behavior_key == "my-behavior"
        assert ctx.get_behavior().version_id == "2.0.0"

    def test_environment_returned(self):
        """Verify that environment is returned from the distribution payload."""
        dist = _make_distribution_ext(
            env_id="env-99",
            env_name="staging",
            config_vars={"DB_URL": "postgres://..."},
        )
        ctx = AionRuntimeContext(distribution_extension_payload=dist)

        assert ctx.get_environment().id == "env-99"
        assert ctx.get_environment().name == "staging"
        assert ctx.get_environment().configuration_variables["DB_URL"] == "postgres://..."

    def test_principal_selector_prefers_daemon_identity(self):
        """Verify that principal selector prefers environment daemon identity."""
        dist = _make_distribution_ext(
            env_id="env-99",
            daemon_agent_identity_id="daemon-1",
        )
        ctx = AionRuntimeContext(distribution_extension_payload=dist)

        assert ctx.get_principal_selector() == "aion://agent/identity/daemon-1"

    def test_principal_selector_uses_environment_without_daemon(self):
        """Verify principal selector falls back to environment id."""
        dist = _make_distribution_ext(env_id="env-99")
        ctx = AionRuntimeContext(distribution_extension_payload=dist)

        assert ctx.get_principal_selector() == "aion://agent/environment/env-99"

    def test_principal_selector_missing_without_environment(self):
        """Verify that principal selector is absent without distribution payload."""
        ctx = AionRuntimeContext()

        assert ctx.get_principal_selector() is None

    def test_no_principal_returns_none_without_losing_payload_records(self):
        """Verify that missing principal identity does not discard payload context."""
        dist = _make_distribution_ext(include_principal=False, include_service=True)
        ctx = AionRuntimeContext(distribution_extension_payload=dist)

        assert ctx.get_principal_identity() is None
        assert ctx.get_service_identity().id == "svc-1"
        assert ctx.get_behavior().behavior_key == "main"
        assert ctx.get_environment().name == "prod"


class TestAionRuntimeContextIsActive:
    def _make_ctx(self, *extension_uris: str) -> AionRuntimeContext:
        """is_extension_active() delegates to extensions.is_active().
        Constructing AionRuntimeExtensions with these URIs directly simulates
        "already registered and verified" - is_active()'s own job here is
        just enum-vs-string normalization and delegation, not verification
        (that's the verifier's job, covered separately in
        test_extension_activation.py)."""
        return AionRuntimeContext(
            extensions=AionRuntimeExtensions({uri: None for uri in extension_uris})
        )

    def test_present_extension_returns_true(self):
        """Verify that present extension returns true."""
        ctx = self._make_ctx(DISTRIBUTION_EXTENSION_URI_V1)
        assert ctx.is_extension_active(AionExtensions.DISTRIBUTION) is True

    def test_absent_extension_returns_false(self):
        """Verify that absent extension returns false."""
        ctx = self._make_ctx(EVENT_EXTENSION_URI_V1)
        assert ctx.is_extension_active(AionExtensions.DISTRIBUTION) is False

    def test_all_extensions_must_be_present(self):
        """Verify that all extensions must be present."""
        ctx = self._make_ctx(DISTRIBUTION_EXTENSION_URI_V1)
        # DISTRIBUTION present, CARDS absent — is_active requires both
        assert ctx.is_extension_active(AionExtensions.DISTRIBUTION, AionExtensions.CARDS) is False

    def test_empty_extensions_returns_true_for_no_args(self):
        """Verify that empty extensions returns true for no args."""
        ctx = self._make_ctx()
        assert ctx.is_extension_active() is True

    def test_plain_string_uri_present_returns_true(self):
        """A raw string URI (e.g. an agent-specific extension) matches directly."""
        ctx = self._make_ctx("aion://extensions/behaviour-evolution/v1")
        assert ctx.is_extension_active("aion://extensions/behaviour-evolution/v1") is True

    def test_plain_string_uri_absent_returns_false(self):
        """A raw string URI not declared on the message is not active."""
        ctx = self._make_ctx(DISTRIBUTION_EXTENSION_URI_V1)
        assert ctx.is_extension_active("aion://extensions/behaviour-evolution/v1") is False

    def test_mixed_enum_and_string_uris_all_required(self):
        """A mix of AionExtensions and plain string URIs are all checked."""
        ctx = self._make_ctx(
            DISTRIBUTION_EXTENSION_URI_V1,
            "aion://extensions/behaviour-evolution/v1",
        )
        assert ctx.is_extension_active(
            AionExtensions.DISTRIBUTION,
            "aion://extensions/behaviour-evolution/v1",
        ) is True
        assert ctx.is_extension_active(
            AionExtensions.DISTRIBUTION,
            "aion://extensions/other/v1",
        ) is False

    def test_graph_kwargs_stored(self):
        """Verify that graph kwargs stored."""
        msg = Message(message_id="m1", role=Role.ROLE_USER)
        inbox = A2AInbox(message=msg, metadata={})
        ctx = AionRuntimeContext(inbox=inbox, thread_id="t-1", config={"k": "v"})
        assert ctx.graph_kwargs["thread_id"] == "t-1"
        assert ctx.graph_kwargs["config"] == {"k": "v"}


_DIST_STRUCT_DATA = {
    "version": "1.0.0",
    "distribution": {
        "id": "dist-1",
        "endpointType": "slack",
        "url": "https://slack.com",
        "identities": [
            {
                "kind": "principal",
                "id": "agent-1",
                "networkType": "aion",
                "organizationId": "org-1",
                "displayName": "Bot",
                "userName": "bot",
            }
        ],
    },
    "behavior": {
        "id": "beh-1",
        "behaviorKey": "main",
        "versionId": "v-1",
    },
    "environment": {
        "id": "env-1",
        "name": "prod",
        "deploymentId": "dep-1",
        "configurationVariables": {},
    },
}


def _make_dist_struct(include_principal: bool = True) -> Struct:
    data = deepcopy(_DIST_STRUCT_DATA)
    if not include_principal:
        data["distribution"]["identities"] = [
            {
                "kind": "service",
                "id": "svc-1",
                "networkType": "slack",
                "organizationId": "org-1",
            }
        ]

    s = Struct()
    ParseDict(data, s)
    return s


def _make_inbox_with_dist(include_event: bool = False, include_principal: bool = True) -> A2AInbox:
    dist_struct = _make_dist_struct(include_principal=include_principal)

    if not include_event:
        return A2AInbox(message=None, metadata={DISTRIBUTION_EXTENSION_URI_V1: dist_struct})

    msg = Message(message_id="msg-1", role=Role.ROLE_USER)
    msg.extensions.append(MESSAGING_EXTENSION_URI_V1)
    msg.metadata.get_or_create_struct(EVENT_EXTENSION_URI_V1).update({
        "type": MESSAGE_EVENT_TYPE_V1,
        "source": "slack://workspace",
        "id": "ev-1",
    })
    part = Part()
    part.metadata.get_or_create_struct(EVENT_EXTENSION_URI_V1).update({
        "schema": MESSAGE_EVENT_PAYLOAD_SCHEMA_V1,
    })
    payload = Struct()
    ParseDict({"userId": "u-1", "contextId": "c-1", "messageId": "m-1", "trajectory": "direct-message"}, payload)
    part.data.struct_value.CopyFrom(payload)
    msg.parts.append(part)

    return A2AInbox(message=msg, metadata={DISTRIBUTION_EXTENSION_URI_V1: dist_struct})


def _make_mock_rc(message=None, metadata=None):
    rc = MagicMock()
    rc.current_task = None
    rc.message = message
    rc.metadata = metadata if metadata is not None else {}
    rc.requested_extensions = frozenset()
    return rc


class TestAionRuntimeContextBuilder:
    def test_none_request_context_returns_none(self):
        """Verify that none request context returns none."""
        assert AionRuntimeContextBuilder.from_request_context(None) is None

    def test_inbox_none_returns_none(self):
        """Verify that inbox none returns none."""
        rc = _make_mock_rc()
        with patch("aion.core.runtime.context.builder.A2AInbox.from_request_context", return_value=None):
            result = AionRuntimeContextBuilder.from_request_context(rc)
        assert result is None

    def test_without_distribution_returns_minimal_context(self):
        """Verify that without distribution returns minimal context."""
        rc = _make_mock_rc()
        result = AionRuntimeContextBuilder.from_request_context(rc)
        assert isinstance(result, AionRuntimeContext)
        assert result.event is None
        assert result.distribution_extension_payload is None

    def test_with_distribution_no_event_returns_context_with_distribution_payload(self):
        """Verify that with distribution no event returns context with payload."""
        inbox = _make_inbox_with_dist(include_event=False)
        rc = _make_mock_rc(metadata={DISTRIBUTION_EXTENSION_URI_V1: _make_dist_struct()})
        with patch("aion.core.runtime.context.builder.A2AInbox.from_request_context", return_value=inbox):
            result = AionRuntimeContextBuilder.from_request_context(rc)
        assert isinstance(result, AionRuntimeContext)
        assert result.event is None
        assert result.distribution_extension_payload is not None
        assert result.get_principal_identity().id == "agent-1"
        assert result.get_behavior().behavior_key == "main"

    def test_with_distribution_and_event_returns_full_context(self):
        """Verify that with distribution and event returns full context."""
        inbox = _make_inbox_with_dist(include_event=True)
        rc = _make_mock_rc(
            message=inbox.message,
            metadata={DISTRIBUTION_EXTENSION_URI_V1: _make_dist_struct()},
        )
        with patch("aion.core.runtime.context.builder.A2AInbox.from_request_context", return_value=inbox):
            result = AionRuntimeContextBuilder.from_request_context(rc)
        assert isinstance(result, AionRuntimeContext)
        assert result.event is not None
        assert result.event.kind == EventKind.MESSAGE
        assert result.distribution_extension_payload is not None

    def test_unrecognized_event_type_sets_event_none_without_discarding_distribution(self):
        """A message declaring messaging active but carrying an unknown event type must
        leave event as None while still populating the distribution extension payload."""
        msg = Message(message_id="msg-fail", role=Role.ROLE_USER)
        msg.extensions.append(MESSAGING_EXTENSION_URI_V1)
        msg.metadata.get_or_create_struct(EVENT_EXTENSION_URI_V1).update({
            "type": "to.unknown.event.type",
            "source": "slack://workspace",
            "id": "ev-fail",
        })
        inbox = _make_inbox_with_dist(include_event=False)
        rc = _make_mock_rc(
            message=msg,
            metadata={DISTRIBUTION_EXTENSION_URI_V1: _make_dist_struct()},
        )
        with patch("aion.core.runtime.context.builder.A2AInbox.from_request_context", return_value=inbox):
            result = AionRuntimeContextBuilder.from_request_context(rc)
        assert result.event is None
        assert result.distribution_extension_payload is not None

    def test_missing_principal_keeps_distribution_payload(self):
        """Verify that missing principal identity does not discard payload records."""
        inbox = _make_inbox_with_dist(include_event=False, include_principal=False)
        rc = _make_mock_rc(
            metadata={DISTRIBUTION_EXTENSION_URI_V1: _make_dist_struct(include_principal=False)}
        )
        with patch("aion.core.runtime.context.builder.A2AInbox.from_request_context", return_value=inbox):
            result = AionRuntimeContextBuilder.from_request_context(rc)

        assert result.get_principal_identity() is None
        assert result.get_service_identity().id == "svc-1"
        assert result.get_behavior().behavior_key == "main"
        assert result.get_environment().name == "prod"

    def test_key_error_in_build_returns_none(self):
        """Verify that key error in build returns none."""
        rc = _make_mock_rc()
        with patch(
            "aion.core.runtime.context.builder.AionRuntimeContextBuilder._build",
            side_effect=KeyError("missing"),
        ):
            result = AionRuntimeContextBuilder.from_request_context(rc)
        assert result is None

    def test_attribute_error_in_build_returns_none(self):
        """Verify that attribute error in build returns none."""
        rc = _make_mock_rc()
        with patch(
            "aion.core.runtime.context.builder.AionRuntimeContextBuilder._build",
            side_effect=AttributeError("attr"),
        ):
            result = AionRuntimeContextBuilder.from_request_context(rc)
        assert result is None

    def test_generic_exception_in_build_returns_none(self):
        """Verify that generic exception in build returns none."""
        rc = _make_mock_rc()
        with patch(
            "aion.core.runtime.context.builder.AionRuntimeContextBuilder._build",
            side_effect=RuntimeError("boom"),
        ):
            result = AionRuntimeContextBuilder.from_request_context(rc)
        assert result is None


def _make_daemon_struct() -> Struct:
    data = {
        "daemon_identity": {
            "kind": "daemon",
            "id": "daemon-1",
            "network_type": "Aion",
            "organization_id": "org-1",
            "display_name": "Inventory Daemon",
        },
        "behavior": {"id": "beh-1", "behavior_key": "main", "version_id": "v-1"},
        "environment": {
            "id": "env-1",
            "name": "prod",
            "deployment_id": "dep-1",
            "configuration_variables": {"llm": "qwen"},
            "daemon_agent_identity_id": "daemon-1",
        },
    }
    s = Struct()
    ParseDict(data, s)
    return s


class TestBuilderExtensionPipeline:
    """Covers the collector/verifier pipeline wired into from_request_context,
    using the DAEMON descriptor aion-core registers for itself."""

    def test_daemon_payload_parsed_into_extensions_and_get_daemon(self):
        # Daemon defaults inactive - opt it in the way AgentManager does from
        # AgentConfig.enabled_extensions.
        aion_a2a_extension_registry.activate([DAEMON_EXTENSION_URI_V1])
        try:
            rc = _make_mock_rc(metadata={DAEMON_EXTENSION_URI_V1: _make_daemon_struct()})
            inbox = A2AInbox(message=None, metadata={DAEMON_EXTENSION_URI_V1: _make_daemon_struct()})

            with patch("aion.core.runtime.context.builder.A2AInbox.from_request_context", return_value=inbox):
                result = AionRuntimeContextBuilder.from_request_context(rc)

            assert result is not None
            assert result.distribution_extension_payload is None
            daemon = result.get_daemon()
            assert daemon is not None
            assert daemon.daemon_identity.id == "daemon-1"
            assert daemon.environment.configuration_variables["llm"] == "qwen"
            assert result.extensions.get(DAEMON_EXTENSION_URI_V1) is daemon
        finally:
            aion_a2a_extension_registry.reset_to_default()

    def test_traceability_payload_parsed_into_extensions_and_get_traceability(self):
        """traceability delivers its payload the same way daemon does
        (params.metadata[uri]) and has a registered descriptor - it must not
        sit merely "active" without being parsed."""
        struct = Struct()
        ParseDict({"traceparent": "00-trace-01"}, struct)
        rc = _make_mock_rc(metadata={TRACEABILITY_EXTENSION_URI_V1: struct})
        inbox = A2AInbox(message=None, metadata={TRACEABILITY_EXTENSION_URI_V1: struct})

        with patch("aion.core.runtime.context.builder.A2AInbox.from_request_context", return_value=inbox):
            result = AionRuntimeContextBuilder.from_request_context(rc)

        traceability = result.get_traceability()
        assert traceability is not None
        assert traceability.traceparent == "00-trace-01"
        assert result.extensions.get(TRACEABILITY_EXTENSION_URI_V1) is traceability

    def test_unmet_requires_raises_and_is_not_swallowed(self):
        """A registered descriptor whose co-activation requirement is unmet
        must reject the request (ExtensionActivationError), not fall through
        the builder's broad except clauses into a silent None context - a
        silent None would make _resolve() treat the request as if no
        extension were active at all."""
        fake_uri = "aion://extensions/test-half-activated/v1"
        descriptor = ExtensionDescriptor(uri=fake_uri, requires=(DAEMON_EXTENSION_URI_V1,))
        aion_a2a_extension_registry.register(descriptor)
        try:
            msg = Message(message_id="m1", role=Role.ROLE_USER)
            msg.extensions.append(fake_uri)
            inbox = A2AInbox(message=msg, metadata={})
            rc = _make_mock_rc(message=msg, metadata={})

            with patch("aion.core.runtime.context.builder.A2AInbox.from_request_context", return_value=inbox):
                with pytest.raises(ExtensionActivationError) as exc_info:
                    AionRuntimeContextBuilder.from_request_context(rc)

            assert exc_info.value.uri == fake_uri
            assert exc_info.value.missing_requires == frozenset({DAEMON_EXTENSION_URI_V1})
        finally:
            aion_a2a_extension_registry._descriptors.pop(fake_uri, None)
            # Drop the default too - a stale default makes any later
            # reset_to_default() KeyError on the unregistered URI.
            aion_a2a_extension_registry._defaults.pop(fake_uri, None)


def _make_evolution_directive_message(
    event_type: str = None,
    payload_schema: str = None,
    payload_overrides: dict = None,
) -> Message:
    """A daemon-scoped directive message: text instruction part + schema-tagged data part."""
    from aion.core.constants.a2a import (
        BEHAVIOUR_EVOLUTION_DIRECTIVE_EVENT_PAYLOAD_SCHEMA_V1,
        BEHAVIOUR_EVOLUTION_DIRECTIVE_EVENT_TYPE_V1,
        BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1,
    )

    msg = Message(message_id="msg-evo-1", role=Role.ROLE_USER)
    msg.extensions.append(BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1)
    msg.metadata.get_or_create_struct(EVENT_EXTENSION_URI_V1).update({
        "type": event_type or BEHAVIOUR_EVOLUTION_DIRECTIVE_EVENT_TYPE_V1,
        "source": "aion://control-plane/reflection",
        "id": "ev-evo-1",
    })

    msg.parts.append(Part(text="Append one short, friendly sentence to the end of README.md."))

    data_part = Part()
    data_part.metadata.get_or_create_struct(EVENT_EXTENSION_URI_V1).update({
        "schema": payload_schema or BEHAVIOUR_EVOLUTION_DIRECTIVE_EVENT_PAYLOAD_SCHEMA_V1,
    })
    payload = Struct()
    payload_dict = {
        "target": {
            "repoUrl": "https://github.com/acme/target-agent.git",
            "baseRef": "HEAD",
        },
        "kind": "feature",
        "mode": "advisory",
    }
    if payload_overrides:
        payload_dict.update(payload_overrides)
    ParseDict(payload_dict, payload)
    data_part.data.struct_value.CopyFrom(payload)
    msg.parts.append(data_part)
    return msg


class TestBuilderEvolutionDirectivePipeline:
    """Covers the behaviour-evolution descriptor's MessagesCollector: a
    daemon-scoped directive event must arrive downstream as a typed Event
    with an EvolutionDirectiveEventPayload."""

    def test_directive_event_parsed_when_evolution_and_daemon_enabled(self):
        from aion.core.a2a.extensions.behaviour_evolution import EvolutionDirectiveEventPayload
        from aion.core.constants.a2a import (
            BEHAVIOUR_EVOLUTION_DIRECTIVE_EVENT_TYPE_V1,
            BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1,
        )

        aion_a2a_extension_registry.activate(
            [BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1, DAEMON_EXTENSION_URI_V1]
        )
        try:
            msg = _make_evolution_directive_message()
            metadata = {DAEMON_EXTENSION_URI_V1: _make_daemon_struct()}
            inbox = A2AInbox(message=msg, metadata=metadata)
            rc = _make_mock_rc(message=msg, metadata=metadata)

            with patch(
                "aion.core.runtime.context.builder.A2AInbox.from_request_context",
                return_value=inbox,
            ):
                result = AionRuntimeContextBuilder.from_request_context(rc)

            assert result is not None
            assert result.is_extension_active(BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1)
            event = result.extensions.get(BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1)
            assert event is not None
            assert event.kind == BEHAVIOUR_EVOLUTION_DIRECTIVE_EVENT_TYPE_V1
            assert isinstance(event.payload, EvolutionDirectiveEventPayload)
            assert event.payload.target.repo_url == "https://github.com/acme/target-agent.git"
            assert event.payload.target.base_ref == "HEAD"
            assert event.payload.kind == "feature"
            assert event.payload.mode == "advisory"
            assert event.payload.scope == "auto"
        finally:
            aion_a2a_extension_registry.reset_to_default()

    def test_directive_stage_parsed_from_payload(self):
        from aion.core.constants.a2a import BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1

        aion_a2a_extension_registry.activate(
            [BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1, DAEMON_EXTENSION_URI_V1]
        )
        try:
            msg = _make_evolution_directive_message(payload_overrides={"scope": "plan"})
            metadata = {DAEMON_EXTENSION_URI_V1: _make_daemon_struct()}
            inbox = A2AInbox(message=msg, metadata=metadata)
            rc = _make_mock_rc(message=msg, metadata=metadata)

            with patch(
                "aion.core.runtime.context.builder.A2AInbox.from_request_context",
                return_value=inbox,
            ):
                result = AionRuntimeContextBuilder.from_request_context(rc)

            event = result.extensions.get(BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1)
            assert event.payload.scope == "plan"
        finally:
            aion_a2a_extension_registry.reset_to_default()

    def test_directive_stage_defaults_to_auto_when_absent(self):
        from aion.core.constants.a2a import BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1

        aion_a2a_extension_registry.activate(
            [BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1, DAEMON_EXTENSION_URI_V1]
        )
        try:
            msg = _make_evolution_directive_message()
            metadata = {DAEMON_EXTENSION_URI_V1: _make_daemon_struct()}
            inbox = A2AInbox(message=msg, metadata=metadata)
            rc = _make_mock_rc(message=msg, metadata=metadata)

            with patch(
                "aion.core.runtime.context.builder.A2AInbox.from_request_context",
                return_value=inbox,
            ):
                result = AionRuntimeContextBuilder.from_request_context(rc)

            event = result.extensions.get(BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1)
            assert event.payload.scope == "auto"
        finally:
            aion_a2a_extension_registry.reset_to_default()

    def test_directive_rejects_invalid_stage(self):
        """Schema-level contract: an out-of-Literal scope value fails pydantic
        validation on the payload model itself."""
        from pydantic import ValidationError

        from aion.core.a2a.extensions.behaviour_evolution import EvolutionDirectiveEventPayload

        with pytest.raises(ValidationError):
            EvolutionDirectiveEventPayload.model_validate({
                "target": {
                    "repo_url": "https://github.com/acme/target-agent.git",
                    "base_ref": "HEAD",
                },
                "kind": "feature",
                "mode": "advisory",
                "scope": "review",
            })

    def test_malformed_directive_fails_closed_through_pipeline(self):
        """A directive part tagged with a known schema but carrying an invalid
        value (scope='review') must fail the request with a real diagnostic:
        the MessagesCollector surfaces the payload validation error as
        ExtensionActivationError instead of swallowing it and letting the
        request read downstream as 'no event on the request at all'."""
        from aion.core.constants.a2a import BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1

        aion_a2a_extension_registry.activate(
            [BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1, DAEMON_EXTENSION_URI_V1]
        )
        try:
            msg = _make_evolution_directive_message(payload_overrides={"scope": "review"})
            metadata = {DAEMON_EXTENSION_URI_V1: _make_daemon_struct()}
            inbox = A2AInbox(message=msg, metadata=metadata)
            rc = _make_mock_rc(message=msg, metadata=metadata)

            with patch(
                "aion.core.runtime.context.builder.A2AInbox.from_request_context",
                return_value=inbox,
            ):
                with pytest.raises(ExtensionActivationError) as exc_info:
                    AionRuntimeContextBuilder.from_request_context(rc)

            assert "failed validation" in str(exc_info.value)
        finally:
            aion_a2a_extension_registry.reset_to_default()

    def test_directive_without_daemon_extension_rejected(self):
        """The daemon co-activation gate: evolution declared alone must raise,
        not silently route the request as a primary task."""
        from aion.core.constants.a2a import BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1

        aion_a2a_extension_registry.activate(
            [BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1, DAEMON_EXTENSION_URI_V1]
        )
        try:
            msg = _make_evolution_directive_message()
            inbox = A2AInbox(message=msg, metadata={})
            rc = _make_mock_rc(message=msg, metadata={})

            with patch(
                "aion.core.runtime.context.builder.A2AInbox.from_request_context",
                return_value=inbox,
            ):
                with pytest.raises(ExtensionActivationError) as exc_info:
                    AionRuntimeContextBuilder.from_request_context(rc)

            assert exc_info.value.uri == BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1
            assert exc_info.value.missing_requires == frozenset({DAEMON_EXTENSION_URI_V1})
        finally:
            aion_a2a_extension_registry.reset_to_default()

    def test_directive_not_collected_when_extension_not_enabled_for_agent(self):
        """Without AgentConfig.enabled_extensions opting evolution in, a request
        declaring it must be rejected as inactive for this agent."""
        from aion.core.constants.a2a import BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1

        aion_a2a_extension_registry.reset_to_default()
        msg = _make_evolution_directive_message()
        inbox = A2AInbox(message=msg, metadata={})
        rc = _make_mock_rc(message=msg, metadata={})

        with patch(
            "aion.core.runtime.context.builder.A2AInbox.from_request_context",
            return_value=inbox,
        ):
            with pytest.raises(ExtensionActivationError) as exc_info:
                AionRuntimeContextBuilder.from_request_context(rc)

        assert exc_info.value.uri == BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1


class TestMessagesCollectorMalformedPayload:
    """MessagesCollector.collect honours the ExtensionPayloadCollector contract:
    a part tagged with a known schema but whose data fails validation surfaces
    as ExtensionActivationError, not a silent drop — while a later valid part
    still wins (multi-part fallback) and a genuinely absent payload returns None."""

    def _collector(self):
        from aion.core.a2a.extensions.behaviour_evolution import EvolutionDirectiveEventPayload
        from aion.core.runtime.context.extensions.descriptors import MessagesCollector

        return MessagesCollector(EvolutionDirectiveEventPayload)

    def _envelope(self, msg):
        from aion.core.constants.a2a import BEHAVIOUR_EVOLUTION_DIRECTIVE_EVENT_TYPE_V1

        msg.metadata.get_or_create_struct(EVENT_EXTENSION_URI_V1).update({
            "type": BEHAVIOUR_EVOLUTION_DIRECTIVE_EVENT_TYPE_V1,
            "source": "aion://control-plane/reflection",
            "id": "ev-evo-mp",
        })

    def _data_part(self, *, scope=None):
        from aion.core.constants.a2a import BEHAVIOUR_EVOLUTION_DIRECTIVE_EVENT_PAYLOAD_SCHEMA_V1

        part = Part()
        part.metadata.get_or_create_struct(EVENT_EXTENSION_URI_V1).update({
            "schema": BEHAVIOUR_EVOLUTION_DIRECTIVE_EVENT_PAYLOAD_SCHEMA_V1,
        })
        payload_dict = {
            "target": {
                "repoUrl": "https://github.com/acme/target-agent.git",
                "baseRef": "HEAD",
            },
            "kind": "feature",
            "mode": "advisory",
        }
        if scope is not None:
            payload_dict["scope"] = scope
        s = Struct()
        ParseDict(payload_dict, s)
        part.data.struct_value.CopyFrom(s)
        return part

    def test_malformed_known_schema_part_raises(self):
        from aion.core.constants.a2a import BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1

        msg = Message(message_id="m-mp-1", role=Role.ROLE_USER)
        self._envelope(msg)
        msg.parts.append(self._data_part(scope="review"))  # invalid enum value
        rc = _make_mock_rc(message=msg)

        with pytest.raises(ExtensionActivationError) as exc_info:
            self._collector().collect(BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1, rc)
        assert "failed validation" in str(exc_info.value)

    def test_later_valid_part_wins_over_earlier_invalid(self):
        """The multi-part fallback the silent drop used to provide must survive:
        an earlier malformed part does not veto a later valid one."""
        from aion.core.constants.a2a import BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1

        msg = Message(message_id="m-mp-2", role=Role.ROLE_USER)
        self._envelope(msg)
        msg.parts.append(self._data_part(scope="review"))  # invalid, first
        msg.parts.append(self._data_part(scope="plan"))  # valid, second
        rc = _make_mock_rc(message=msg)

        event = self._collector().collect(BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1, rc)
        assert event is not None
        assert event.payload.scope == "plan"

    def test_absent_payload_returns_none(self):
        """No part carries a known schema: genuinely no event for us — still
        None, not an error (distinct from the malformed-but-present case)."""
        from aion.core.constants.a2a import BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1

        msg = Message(message_id="m-mp-3", role=Role.ROLE_USER)
        self._envelope(msg)
        msg.parts.append(Part(text="just an instruction, no data part"))
        rc = _make_mock_rc(message=msg)

        assert self._collector().collect(BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1, rc) is None


class TestUnknownExtensions:
    """Declared URIs no registered descriptor claims: never an error, never
    active - but carried on the runtime context as inert UnknownExtension
    entries instead of being silently dropped at parsing."""

    UNKNOWN_URI = "https://example.com/extensions/vendor/custom/1.0.0"

    def _build(self):
        from aion.core.runtime.context.extensions import UnknownExtension  # noqa: F401

        msg = Message(message_id="m-unknown", role=Role.ROLE_USER)
        msg.extensions.append(self.UNKNOWN_URI)
        inbox = A2AInbox(message=msg, metadata={})
        rc = _make_mock_rc(message=msg, metadata={})

        with patch(
            "aion.core.runtime.context.builder.A2AInbox.from_request_context",
            return_value=inbox,
        ):
            return AionRuntimeContextBuilder.from_request_context(rc)

    def test_unknown_extension_is_carried_but_not_active(self):
        from aion.core.runtime.context.extensions import UnknownExtension

        result = self._build()

        assert result is not None
        assert result.is_extension_active(self.UNKNOWN_URI) is False
        carried = result.extensions.get(self.UNKNOWN_URI)
        assert isinstance(carried, UnknownExtension)
        assert carried.uri == self.UNKNOWN_URI
        assert self.UNKNOWN_URI in result.extensions
        assert self.UNKNOWN_URI in list(result.extensions)
        assert [u.uri for u in result.extensions.unknown] == [self.UNKNOWN_URI]

    def test_undeclared_extension_is_absent_entirely(self):
        result = self._build()

        other = "https://example.com/extensions/vendor/other/1.0.0"
        assert result.extensions.get(other) is None
        assert other not in result.extensions


class TestUnavailableExtensionVerification:
    """An extension that is enabled but marked unavailable must reject the
    request with the recorded, extension-authored reason."""

    def test_active_but_unavailable_extension_rejected_with_reason(self):
        fake_uri = "aion://extensions/test-unavailable-pipeline/v1"
        aion_a2a_extension_registry.register(ExtensionDescriptor(uri=fake_uri, active=True))
        aion_a2a_extension_registry.mark_unavailable(fake_uri, "its toolkit is not installed")
        try:
            msg = Message(message_id="m-unavail", role=Role.ROLE_USER)
            msg.extensions.append(fake_uri)
            inbox = A2AInbox(message=msg, metadata={})
            rc = _make_mock_rc(message=msg, metadata={})

            with patch(
                "aion.core.runtime.context.builder.A2AInbox.from_request_context",
                return_value=inbox,
            ):
                with pytest.raises(ExtensionActivationError, match="its toolkit is not installed"):
                    AionRuntimeContextBuilder.from_request_context(rc)
        finally:
            aion_a2a_extension_registry._descriptors.pop(fake_uri, None)
            aion_a2a_extension_registry._defaults.pop(fake_uri, None)

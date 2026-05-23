"""Tests for runtime context event and distribution payload handling."""

import pytest
from copy import deepcopy
from unittest.mock import MagicMock, patch
from a2a.types import Message, Part, Role
from google.protobuf.json_format import ParseDict
from google.protobuf.struct_pb2 import Struct

from aion.core.constants.a2a import (
    CARDS_EXTENSION_URI_V1,
    DISTRIBUTION_EXTENSION_URI_V1,
    EVENT_EXTENSION_URI_V1,
    MESSAGE_EVENT_PAYLOAD_SCHEMA_V1,
    MESSAGE_EVENT_TYPE_V1,
    REACTION_EVENT_TYPE_V1,
    REACTION_EVENT_PAYLOAD_SCHEMA_V1,
)
from aion.core.runtime.context.builder import AionRuntimeContextBuilder
from aion.core.runtime.context.models import (
    AionExtensions,
    AionRuntimeContext,
    EventKind,
)
from aion.core.runtime.context.utils import extract_event
from aion.core.types.a2a.extensions.distribution import (
    PrincipalIdentity,
    Behavior,
    DistributionExtensionV1,
    Distribution,
    Environment,
    ServiceIdentity,
)
from aion.core.types.a2a.models import A2AInbox


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
        ctx = AionRuntimeContext(distributionExtensionPayload=dist)

        assert ctx.distributionExtensionPayload is dist
        assert ctx.get_distribution().id == "dist-1"

    def test_principal_identity_extracted(self):
        """Verify that principal identity is read from the distribution payload."""
        dist = _make_distribution_ext(agent_id="agent-abc", behavior_key="main", version_id="v-3")
        ctx = AionRuntimeContext(distributionExtensionPayload=dist)
        identity = ctx.get_principal_identity()

        assert identity.id == "agent-abc"
        assert identity.display_name == "Bot"
        assert identity.user_name == "bot"
        assert identity.network_type == "aion"

    def test_service_identity_extracted(self):
        """Verify that service identity is read from the distribution payload."""
        dist = _make_distribution_ext(include_service=True)
        ctx = AionRuntimeContext(distributionExtensionPayload=dist)

        service_identity = ctx.get_service_identity()

        assert service_identity.id == "svc-1"
        assert service_identity.network_type == "slack"

    def test_behavior_returned(self):
        """Verify that behavior is returned from the distribution payload."""
        dist = _make_distribution_ext(behavior_key="my-behavior", version_id="2.0.0")
        ctx = AionRuntimeContext(distributionExtensionPayload=dist)

        assert ctx.get_behavior().behavior_key == "my-behavior"
        assert ctx.get_behavior().version_id == "2.0.0"

    def test_environment_returned(self):
        """Verify that environment is returned from the distribution payload."""
        dist = _make_distribution_ext(
            env_id="env-99",
            env_name="staging",
            config_vars={"DB_URL": "postgres://..."},
        )
        ctx = AionRuntimeContext(distributionExtensionPayload=dist)

        assert ctx.get_environment().id == "env-99"
        assert ctx.get_environment().name == "staging"
        assert ctx.get_environment().configuration_variables["DB_URL"] == "postgres://..."

    def test_no_principal_returns_none_without_losing_payload_records(self):
        """Verify that missing principal identity does not discard payload context."""
        dist = _make_distribution_ext(include_principal=False, include_service=True)
        ctx = AionRuntimeContext(distributionExtensionPayload=dist)

        assert ctx.get_principal_identity() is None
        assert ctx.get_service_identity().id == "svc-1"
        assert ctx.get_behavior().behavior_key == "main"
        assert ctx.get_environment().name == "prod"


class TestAionRuntimeContextIsActive:
    def _make_ctx(self, *extension_uris: str) -> AionRuntimeContext:
        msg = Message(message_id="m1", role=Role.ROLE_USER)
        for uri in extension_uris:
            msg.extensions.append(uri)
        inbox = A2AInbox(message=msg, metadata={})
        return AionRuntimeContext(inbox=inbox)

    def test_present_extension_returns_true(self):
        """Verify that present extension returns true."""
        ctx = self._make_ctx(DISTRIBUTION_EXTENSION_URI_V1)
        assert ctx.is_active(AionExtensions.DISTRIBUTION) is True

    def test_absent_extension_returns_false(self):
        """Verify that absent extension returns false."""
        ctx = self._make_ctx(EVENT_EXTENSION_URI_V1)
        assert ctx.is_active(AionExtensions.DISTRIBUTION) is False

    def test_all_extensions_must_be_present(self):
        """Verify that all extensions must be present."""
        ctx = self._make_ctx(DISTRIBUTION_EXTENSION_URI_V1)
        # DISTRIBUTION present, CARDS absent — is_active requires both
        assert ctx.is_active(AionExtensions.DISTRIBUTION, AionExtensions.CARDS) is False

    def test_empty_extensions_returns_true_for_no_args(self):
        """Verify that empty extensions returns true for no args."""
        ctx = self._make_ctx()
        assert ctx.is_active() is True

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
        assert result.distributionExtensionPayload is None

    def test_with_distribution_no_event_returns_context_with_distribution_payload(self):
        """Verify that with distribution no event returns context with payload."""
        inbox = _make_inbox_with_dist(include_event=False)
        rc = _make_mock_rc(metadata={DISTRIBUTION_EXTENSION_URI_V1: _make_dist_struct()})
        with patch("aion.core.runtime.context.builder.A2AInbox.from_request_context", return_value=inbox):
            result = AionRuntimeContextBuilder.from_request_context(rc)
        assert isinstance(result, AionRuntimeContext)
        assert result.event is None
        assert result.distributionExtensionPayload is not None
        assert result.get_principal_identity().id == "agent-1"
        assert result.get_behavior().behavior_key == "main"

    def test_with_distribution_and_event_returns_full_context(self):
        """Verify that with distribution and event returns full context."""
        inbox = _make_inbox_with_dist(include_event=True)
        with patch("aion.core.runtime.context.builder.A2AInbox.from_request_context", return_value=inbox):
            result = AionRuntimeContextBuilder.from_request_context(_make_mock_rc())
        assert isinstance(result, AionRuntimeContext)
        assert result.event is not None
        assert result.event.kind == EventKind.MESSAGE
        assert result.distributionExtensionPayload is not None

    def test_key_error_in_build_returns_none(self):
        """Verify that key error in build returns none."""
        rc = _make_mock_rc()
        with patch(
            "aion.core.runtime.context.builder.AionRuntimeContextBuilder._build_without_distribution",
            side_effect=KeyError("missing"),
        ):
            result = AionRuntimeContextBuilder.from_request_context(rc)
        assert result is None

    def test_attribute_error_in_build_returns_none(self):
        """Verify that attribute error in build returns none."""
        rc = _make_mock_rc()
        with patch(
            "aion.core.runtime.context.builder.AionRuntimeContextBuilder._build_without_distribution",
            side_effect=AttributeError("attr"),
        ):
            result = AionRuntimeContextBuilder.from_request_context(rc)
        assert result is None

    def test_generic_exception_in_build_returns_none(self):
        """Verify that generic exception in build returns none."""
        rc = _make_mock_rc()
        with patch(
            "aion.core.runtime.context.builder.AionRuntimeContextBuilder._build_without_distribution",
            side_effect=RuntimeError("boom"),
        ):
            result = AionRuntimeContextBuilder.from_request_context(rc)
        assert result is None


class TestBuildFromDistribution:
    def test_without_event_sets_event_none(self):
        """Verify that without event sets event none."""
        inbox = _make_inbox_with_dist(include_event=False)
        result = AionRuntimeContextBuilder._build_from_distribution(inbox)
        assert isinstance(result, AionRuntimeContext)
        assert result.event is None
        assert result.distributionExtensionPayload is not None

    def test_with_event_populates_event(self):
        """Verify that with event populates event."""
        inbox = _make_inbox_with_dist(include_event=True)
        result = AionRuntimeContextBuilder._build_from_distribution(inbox)
        assert result.event is not None
        assert result.event.kind == EventKind.MESSAGE
        assert result.event.source == "slack://workspace"

    def test_extract_event_failure_sets_event_none(self):
        """Verify that extract event failure sets event none."""
        inbox = _make_inbox_with_dist(include_event=True)
        with patch("aion.core.runtime.context.builder.extract_event", side_effect=ValueError("parse error")):
            result = AionRuntimeContextBuilder._build_from_distribution(inbox)
        assert result.event is None
        assert result.distributionExtensionPayload is not None

    def test_missing_principal_keeps_distribution_payload(self):
        """Verify that missing principal identity does not discard payload records."""
        inbox = _make_inbox_with_dist(include_event=False, include_principal=False)
        result = AionRuntimeContextBuilder._build_from_distribution(inbox)

        assert result.get_principal_identity() is None
        assert result.get_service_identity().id == "svc-1"
        assert result.get_behavior().behavior_key == "main"
        assert result.get_environment().name == "prod"

    def test_distribution_payload_fields_correct(self):
        """Verify that distribution payload fields are exposed through helpers."""
        inbox = _make_inbox_with_dist(include_event=False)
        result = AionRuntimeContextBuilder._build_from_distribution(inbox)

        assert result.distributionExtensionPayload.distribution.id == "dist-1"
        assert result.get_principal_identity().id == "agent-1"
        assert result.get_behavior().behavior_key == "main"
        assert result.get_environment().name == "prod"


class TestBuildWithoutDistribution:
    def test_returns_minimal_context(self):
        """Verify that returns minimal context."""
        inbox = A2AInbox(message=None, metadata={})
        result = AionRuntimeContextBuilder._build_without_distribution(inbox)
        assert isinstance(result, AionRuntimeContext)
        assert result.event is None
        assert result.distributionExtensionPayload is None
        assert result.inbox is inbox

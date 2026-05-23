"""Tests for aion.shared.types.a2a — models, extensions, request/response, enums.

Focus areas:
  Enums (aion.shared.types.a2a.enums):
    - MessageType, ArtifactId, ArtifactName values
    - A2AEventType, A2AMetadataKey, ArtifactStreamingStatus values

  A2A Models (aion.shared.types.a2a.models):
    - A2AInbox: construction, frozen immutability, from_request_context
    - A2AOutbox: optional task/message fields, JSON serialization
    - ConversationTaskStatus: state serialized to string name
    - Conversation: construction, history/artifacts list, JSON round-trip
    - ContextsList: root model, list access
    - A2AManifest: construction, endpoints dict, JSON serialization

  Extensions:
    event.py — EventMessageMetadataV1, EventPartMetadataV1 (schema alias)
    messaging.py — MessageEventPayload, ReactionEventPayload, CommandEventPayload,
                   SourceSystemEventPayload, MessageActionPayload, ReactionActionPayload
    traceability.py — TraceabilityExtensionV1: version default, traceparent/tracestate/baggage
    cards.py — CardActionEventPayload
    distribution.py — PrincipalIdentity, ServiceIdentity (discriminated union),
                      Distribution, Behavior, Environment, DistributionExtensionV1

  Request / Response (aion.shared.types.a2a.request, request_params, response):
    - GetContextParams: required context_id, optional pagination fields
    - GetContextsListParams: optional pagination
    - GetContextRequest: jsonrpc default, method literal
    - GetContextsListRequest: method literal
    - GetContextSuccessResponse: result is Conversation
    - GetContextsListSuccessResponse: result is ContextsList
"""

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from a2a.types import Artifact, Message, Role, Task, TaskState, TaskStatus

from aion.core.types.a2a.enums import (
    A2AEventType,
    A2AMetadataKey,
    ArtifactId,
    ArtifactName,
    ArtifactStreamingStatus,
    ArtifactStreamingStatusReason,
    MessageType,
)
from aion.core.types.a2a.extensions.cards import CardActionEventPayload
from aion.core.types.a2a.extensions.distribution import (
    PrincipalIdentity,
    Behavior,
    DistributionExtensionV1,
    Distribution,
    Environment,
    ServiceIdentity,
)
from aion.core.types.a2a.extensions.event import EventMessageMetadataV1, EventPartMetadataV1
from aion.core.types.a2a.extensions.messaging import (
    CommandEventPayload,
    MessageActionPayload,
    MessageEventPayload,
    ReactionActionPayload,
    ReactionEventPayload,
    SourceSystemEventPayload,
)
from aion.core.types.a2a.extensions.traceability import TraceabilityExtensionV1, TraceStateEntry
from aion.core.types.a2a.models import (
    A2AInbox,
    A2AManifest,
    A2AOutbox,
    Conversation,
    ConversationTaskStatus,
    ContextsList,
)
from aion.core.types.a2a.request import GetContextRequest, GetContextsListRequest
from aion.core.types.a2a.request_params import GetContextParams, GetContextsListParams
from aion.core.types.a2a.response import GetContextSuccessResponse, GetContextsListSuccessResponse


class TestEnums:
    def test_message_type_values(self):
        """MessageType enum has expected string values for all members."""
        assert MessageType.MESSAGE == "message"
        assert MessageType.EVENT == "event"
        assert MessageType.LANGRAPH_VALUES == "langraph_values"

    def test_artifact_id_values(self):
        """ArtifactId enum members match the expected aion-prefixed URI strings."""
        assert ArtifactId.STREAM_DELTA == "aion:stream-delta"
        assert ArtifactId.EPHEMERAL_MESSAGE == "aion:ephemeral-message"

    def test_artifact_name_values(self):
        """ArtifactName enum members hold human-readable display strings."""
        assert ArtifactName.MESSAGE_RESULT == "Message Result"
        assert ArtifactName.STREAM_DELTA == "Stream Delta"

    def test_a2a_event_type_values(self):
        """A2AEventType enum members match their wire-format string values."""
        assert A2AEventType.MESSAGES == "messages"
        assert A2AEventType.INTERRUPT == "interrupt"
        assert A2AEventType.COMPLETE == "complete"

    def test_a2a_metadata_key_values(self):
        """A2AMetadataKey enum members carry the expected aion-namespaced key strings."""
        assert A2AMetadataKey.MESSAGE_TYPE == "aion:messageType"
        assert A2AMetadataKey.NETWORK == "aion:network"
        assert A2AMetadataKey.DISTRIBUTION == "aion:distribution"

    def test_artifact_streaming_status_values(self):
        """ArtifactStreamingStatus enum has correct finalized and active values."""
        assert ArtifactStreamingStatus.FINALIZED == "finalized"
        assert ArtifactStreamingStatus.ACTIVE == "active"

    def test_artifact_streaming_status_reason_values(self):
        """ArtifactStreamingStatusReason enum members match their string values."""
        assert ArtifactStreamingStatusReason.INTERRUPTED == "interrupted"
        assert ArtifactStreamingStatusReason.COMPLETE_MESSAGE == "complete_message"
        assert ArtifactStreamingStatusReason.CHUNK_STREAMING == "chunk_streaming"


class TestA2AInbox:
    def test_empty_construction(self):
        """A2AInbox constructed with no args has None task/message and empty metadata."""
        inbox = A2AInbox()
        assert inbox.task is None
        assert inbox.message is None
        assert inbox.metadata == {}

    def test_metadata_default_is_empty_dict(self):
        """Default metadata on A2AInbox is a dict instance."""
        inbox = A2AInbox()
        assert isinstance(inbox.metadata, dict)

    def test_frozen_prevents_mutation(self):
        """A2AInbox is frozen; assigning to any field raises an exception."""
        inbox = A2AInbox()
        with pytest.raises(Exception):
            inbox.task = MagicMock()

    def test_from_request_context_with_no_task_or_message(self):
        """from_request_context returns inbox with None task/message when context has neither."""
        ctx = MagicMock()
        ctx.current_task = None
        ctx.message = None
        ctx.metadata = {}

        inbox = A2AInbox.from_request_context(ctx)
        assert inbox.task is None
        assert inbox.message is None

    def test_from_request_context_copies_metadata(self):
        """from_request_context copies the request context metadata into the inbox."""
        ctx = MagicMock()
        ctx.current_task = None
        ctx.message = None
        ctx.metadata = {"key": "value"}

        inbox = A2AInbox.from_request_context(ctx)
        assert inbox.metadata == {"key": "value"}

    def test_from_request_context_with_task(self):
        """from_request_context populates inbox.task from the request context's current_task."""
        task = Task(id="t1", context_id="ctx-1", status=TaskStatus(state=TaskState.TASK_STATE_WORKING))
        ctx = MagicMock()
        ctx.current_task = task
        ctx.message = None
        ctx.metadata = {}

        inbox = A2AInbox.from_request_context(ctx)
        assert inbox.task is not None
        assert inbox.task.id == "t1"

    def test_from_request_context_deepcopies_task(self):
        """from_request_context deep-copies the task so inbox.task is not the same object."""
        task = Task(id="t1", context_id="ctx-1", status=TaskStatus(state=TaskState.TASK_STATE_WORKING))
        ctx = MagicMock()
        ctx.current_task = task
        ctx.message = None
        ctx.metadata = {}

        inbox = A2AInbox.from_request_context(ctx)
        # Deepcopy: not the same object
        assert inbox.task is not task


class TestA2AOutbox:
    def test_empty_construction(self):
        """A2AOutbox constructed with no args has None task and message."""
        outbox = A2AOutbox()
        assert outbox.task is None
        assert outbox.message is None

    def test_json_serialization_with_no_fields(self):
        """model_dump on an empty A2AOutbox includes a 'task' key with None value."""
        outbox = A2AOutbox()
        data = outbox.model_dump()
        assert "task" in data
        assert data["task"] is None

    def test_construction_with_message(self):
        """A2AOutbox constructed with a message stores that message."""
        msg = Message(role=Role.ROLE_AGENT, parts=[])
        outbox = A2AOutbox(message=msg)
        assert outbox.message is not None


class TestConversationTaskStatus:
    def test_state_stored_as_int(self):
        """ConversationTaskStatus stores the TaskState value as an integer."""
        status = ConversationTaskStatus(state=TaskState.TASK_STATE_COMPLETED)
        assert isinstance(status.state, int)

    def test_state_serialized_to_name_string(self):
        """model_dump serializes the state integer to its TaskState enum name string."""
        status = ConversationTaskStatus(state=TaskState.TASK_STATE_COMPLETED)
        data = status.model_dump()
        assert data["state"] == "TASK_STATE_COMPLETED"

    def test_working_state_serialized(self):
        """TASK_STATE_WORKING is serialized to its name string in model_dump."""
        status = ConversationTaskStatus(state=TaskState.TASK_STATE_WORKING)
        data = status.model_dump()
        assert data["state"] == "TASK_STATE_WORKING"

    def test_failed_state_serialized(self):
        """TASK_STATE_FAILED is serialized to its name string in model_dump."""
        status = ConversationTaskStatus(state=TaskState.TASK_STATE_FAILED)
        data = status.model_dump()
        assert data["state"] == "TASK_STATE_FAILED"


class TestConversation:
    def _status(self) -> ConversationTaskStatus:
        return ConversationTaskStatus(state=TaskState.TASK_STATE_WORKING)

    def test_basic_construction(self):
        """Conversation with context_id and status initializes with empty history and artifacts."""
        conv = Conversation(context_id="ctx-1", status=self._status())
        assert conv.context_id == "ctx-1"
        assert conv.history == []
        assert conv.artifacts == []

    def test_context_id_required(self):
        """Conversation raises an exception when context_id is omitted."""
        with pytest.raises(Exception):
            Conversation(status=self._status())

    def test_status_required(self):
        """Conversation raises an exception when status is omitted."""
        with pytest.raises(Exception):
            Conversation(context_id="ctx-1")

    def test_history_and_artifacts_default_empty(self):
        """Conversation history and artifacts default to empty lists."""
        conv = Conversation(context_id="ctx-1", status=self._status())
        assert isinstance(conv.history, list)
        assert isinstance(conv.artifacts, list)

    def test_json_serialization(self):
        """Conversation serializes to camelCase JSON with state as name string."""
        conv = Conversation(context_id="ctx-42", status=self._status())
        # Verify camelCase serialization and nested state name
        data = json.loads(conv.model_dump_json())
        assert data["contextId"] == "ctx-42"
        assert data["status"]["state"] == "TASK_STATE_WORKING"
        assert data["history"] == []


class TestContextsList:
    def test_holds_list_of_strings(self):
        """ContextsList root holds the provided list of context ID strings."""
        cl = ContextsList(root=["ctx-1", "ctx-2"])
        assert cl.root == ["ctx-1", "ctx-2"]

    def test_empty_list(self):
        """ContextsList accepts an empty list without error."""
        cl = ContextsList(root=[])
        assert cl.root == []

    def test_json_serialization(self):
        """ContextsList serializes to a plain list via model_dump."""
        cl = ContextsList(root=["a", "b"])
        data = cl.model_dump()
        assert data == ["a", "b"]


class TestA2AManifest:
    def test_basic_construction(self):
        """A2AManifest with api_version and name has empty endpoints by default."""
        manifest = A2AManifest(api_version="1.0", name="my-service")
        assert manifest.api_version == "1.0"
        assert manifest.name == "my-service"
        assert manifest.endpoints == {}

    def test_with_endpoints(self):
        """A2AManifest stores the provided endpoints dict."""
        manifest = A2AManifest(
            api_version="1.0",
            name="svc",
            endpoints={"agent1": "/agents/agent1"},
        )
        assert manifest.endpoints["agent1"] == "/agents/agent1"

    def test_json_serialization(self):
        """A2AManifest serializes with camelCase aliases via model_dump_json."""
        manifest = A2AManifest(api_version="2.0", name="test-svc")
        # A2ABaseModel serializes with camelCase aliases
        data = json.loads(manifest.model_dump_json())
        assert data["apiVersion"] == "2.0"
        assert data["name"] == "test-svc"

    def test_missing_required_fields_raises(self):
        """A2AManifest raises an exception when either api_version or name is missing."""
        with pytest.raises(Exception):
            A2AManifest(name="only-name")

        with pytest.raises(Exception):
            A2AManifest(api_version="1.0")


class TestEventExtensions:
    def test_event_message_metadata(self):
        """EventMessageMetadataV1 stores type, source, and id fields correctly."""
        m = EventMessageMetadataV1(type="dm", source="slack", id="evt-1")
        assert m.type == "dm"
        assert m.source == "slack"
        assert m.id == "evt-1"

    def test_event_part_metadata_schema_alias(self):
        """EventPartMetadataV1 accepts 'schema' as an alias and exposes it as schema_uri."""
        # Field is aliased as "schema" in the model
        m = EventPartMetadataV1(**{"schema": "https://example.com/schema/v1"})
        assert m.schema_uri == "https://example.com/schema/v1"

    def test_event_part_metadata_json_uses_alias(self):
        """model_dump with by_alias=True uses 'schema' key for the schema_uri field."""
        m = EventPartMetadataV1(**{"schema": "https://example.com/schema/v1"})
        data = m.model_dump(by_alias=True)
        assert data["schema"] == "https://example.com/schema/v1"

    def test_event_message_metadata_json_round_trip(self):
        """EventMessageMetadataV1 survives a model_dump / model_validate round-trip."""
        m = EventMessageMetadataV1(type="reply", source="telegram", id="x")
        data = m.model_dump()
        restored = EventMessageMetadataV1.model_validate(data)
        assert restored.id == "x"


class TestMessagingModels:
    def test_message_event(self):
        """MessageEventPayload stores required fields and defaults parent_context_id to None."""
        p = MessageEventPayload(
            user_id="u1",
            context_id="ctx-1",
            message_id="msg-1",
            trajectory="direct-message",
        )
        assert p.user_id == "u1"
        assert p.trajectory == "direct-message"
        assert p.parent_context_id is None

    def test_message_event_with_parent(self):
        """MessageEventPayload stores parent_context_id when provided."""
        p = MessageEventPayload(
            user_id="u1",
            context_id="ctx-1",
            message_id="msg-1",
            trajectory="reply",
            parent_context_id="parent-ctx",
        )
        assert p.parent_context_id == "parent-ctx"

    def test_reaction_event(self):
        """ReactionEventPayload stores reaction fields and defaults optional fields to None."""
        r = ReactionEventPayload(
            user_id="u1",
            context_id="ctx-1",
            message_id="msg-1",
            reaction_key=":thumbsup:",
            action="added",
        )
        assert r.action == "added"
        assert r.display_value is None
        assert r.is_custom is None

    def test_reaction_event_with_custom(self):
        """ReactionEventPayload stores is_custom and display_value when provided."""
        r = ReactionEventPayload(
            user_id="u1",
            context_id="ctx-1",
            message_id="msg-1",
            reaction_key=":custom:",
            action="added",
            is_custom=True,
            display_value="👍",
        )
        assert r.is_custom is True
        assert r.display_value == "👍"

    def test_command_event(self):
        """CommandEventPayload stores command and defaults optional fields to None."""
        c = CommandEventPayload(
            user_id="u1",
            context_id="ctx-1",
            command="/start",
        )
        assert c.command == "/start"
        assert c.arguments is None
        assert c.invocation_id is None

    def test_command_event_with_args(self):
        """CommandEventPayload stores arguments and invocation_id when provided."""
        c = CommandEventPayload(
            user_id="u1",
            context_id="ctx-1",
            command="/run",
            arguments="--verbose",
            invocation_id="inv-123",
        )
        assert c.arguments == "--verbose"
        assert c.invocation_id == "inv-123"

    def test_source_system_event(self):
        """SourceSystemEventPayload stores provider and raw event dict."""
        p = SourceSystemEventPayload(
            provider="slack",
            event={"type": "message", "text": "hello"},
        )
        assert p.provider == "slack"
        assert p.event["type"] == "message"

    def test_message_action_payload(self):
        """MessageActionPayload stores trajectory and defaults user_id to None."""
        a = MessageActionPayload(
            trajectory="conversation",
            context_id="ctx-1",
        )
        assert a.trajectory == "conversation"
        assert a.user_id is None

    def test_message_action_payload_with_user(self):
        """MessageActionPayload stores user_id and reply_to_message_id when provided."""
        a = MessageActionPayload(
            trajectory="direct-message",
            context_id="ctx-1",
            user_id="u1",
            reply_to_message_id="msg-old",
        )
        assert a.user_id == "u1"
        assert a.reply_to_message_id == "msg-old"

    def test_reaction_action_payload_add(self):
        """ReactionActionPayload stores 'add' as the operation."""
        r = ReactionActionPayload(
            context_id="ctx-1",
            message_id="msg-1",
            reaction_key=":heart:",
            operation="add",
        )
        assert r.operation == "add"

    def test_reaction_action_payload_remove(self):
        """ReactionActionPayload stores 'remove' as the operation."""
        r = ReactionActionPayload(
            context_id="ctx-1",
            message_id="msg-1",
            reaction_key=":heart:",
            operation="remove",
        )
        assert r.operation == "remove"

    def test_reaction_action_payload_invalid_operation_raises(self):
        """ReactionActionPayload raises an exception for an unrecognized operation value."""
        with pytest.raises(Exception):
            ReactionActionPayload(
                context_id="ctx-1",
                message_id="msg-1",
                reaction_key=":x:",
                operation="invalid",
            )

    def test_json_round_trip_message_event(self):
        """MessageEventPayload survives a model_dump / model_validate round-trip."""
        p = MessageEventPayload(
            user_id="u1", context_id="ctx-1", message_id="m1", trajectory="timeline"
        )
        data = p.model_dump()
        restored = MessageEventPayload.model_validate(data)
        assert restored.user_id == "u1"
        assert restored.trajectory == "timeline"


class TestTraceabilityExtension:
    def test_default_version(self):
        """TraceabilityExtensionV1 defaults version to '1.0.0'."""
        t = TraceabilityExtensionV1()
        assert t.version == "1.0.0"

    def test_all_optional_fields_none(self):
        """TraceabilityExtensionV1 has None traceparent, tracestate, and baggage by default."""
        t = TraceabilityExtensionV1()
        assert t.traceparent is None
        assert t.tracestate is None
        assert t.baggage is None

    def test_traceparent_set(self):
        """TraceabilityExtensionV1 stores a provided traceparent string."""
        t = TraceabilityExtensionV1(traceparent="00-abc-def-01")
        assert t.traceparent == "00-abc-def-01"

    def test_tracestate_entries(self):
        """TraceabilityExtensionV1 stores a list of TraceStateEntry objects."""
        entries = [TraceStateEntry(key="vendor1", value="val1")]
        t = TraceabilityExtensionV1(tracestate=entries)
        assert len(t.tracestate) == 1
        assert t.tracestate[0].key == "vendor1"

    def test_baggage_dict(self):
        """TraceabilityExtensionV1 stores a baggage dict with the provided key-value pairs."""
        t = TraceabilityExtensionV1(baggage={"key1": "val1", "key2": "val2"})
        assert t.baggage["key1"] == "val1"

    def test_trace_state_entry_construction(self):
        """TraceStateEntry stores key and value fields correctly."""
        entry = TraceStateEntry(key="k", value="v")
        assert entry.key == "k"
        assert entry.value == "v"

    def test_json_round_trip(self):
        """TraceabilityExtensionV1 survives a model_dump / model_validate round-trip."""
        t = TraceabilityExtensionV1(
            traceparent="00-abc-def-01",
            baggage={"aion.sender.id": "u1"},
        )
        data = t.model_dump()
        restored = TraceabilityExtensionV1.model_validate(data)
        assert restored.traceparent == "00-abc-def-01"
        assert restored.baggage["aion.sender.id"] == "u1"


class TestCardsExtension:
    def test_card_action_event(self):
        """CardActionEventPayload stores action_id and defaults parent_context_id to None."""
        p = CardActionEventPayload(
            user_id="u1",
            context_id="ctx-1",
            action_id="btn-submit",
        )
        assert p.action_id == "btn-submit"
        assert p.parent_context_id is None

    def test_with_parent_context(self):
        """CardActionEventPayload stores parent_context_id when provided."""
        p = CardActionEventPayload(
            user_id="u1",
            context_id="ctx-1",
            action_id="btn-1",
            parent_context_id="parent-ctx",
        )
        assert p.parent_context_id == "parent-ctx"

    def test_missing_required_field_raises(self):
        """CardActionEventPayload raises an exception when action_id is omitted."""
        with pytest.raises(Exception):
            CardActionEventPayload(user_id="u1", context_id="ctx-1")  # missing action_id

    def test_json_round_trip(self):
        """CardActionEventPayload survives a model_dump / model_validate round-trip."""
        p = CardActionEventPayload(user_id="u", context_id="c", action_id="a")
        data = p.model_dump()
        restored = CardActionEventPayload.model_validate(data)
        assert restored.action_id == "a"


def _make_distribution() -> Distribution:
    identity = PrincipalIdentity(
        kind="principal",
        id="agent-1",
        network_type="slack",
        organization_id="org-1",
    )
    return Distribution(
        id="dist-1",
        endpoint_type="webhook",
        url="https://example.com",
        identities=[identity],
    )


def _make_behavior() -> Behavior:
    return Behavior(
        id="beh-1",
        behavior_key="default",
        version_id="v1",
    )


def _make_environment() -> Environment:
    return Environment(
        id="env-1",
        name="production",
        deployment_id="dep-1",
        configuration_variables={"KEY": "VALUE"},
    )


class TestDistributionExtension:
    def test_principal_identity(self):
        """PrincipalIdentity stores kind, id, network_type and defaults optional fields to None."""
        r = PrincipalIdentity(
            kind="principal",
            id="a1",
            network_type="slack",
            organization_id="org-1",
        )
        assert r.kind == "principal"
        assert r.represented_user_id is None
        assert r.display_name is None

    def test_service_identity(self):
        """ServiceIdentity stores kind correctly."""
        r = ServiceIdentity(
            kind="service",
            id="s1",
            network_type="webhook",
            organization_id="org-2",
        )
        assert r.kind == "service"

    def test_distribution_with_identities(self):
        """Distribution stores identities list and id correctly."""
        rec = _make_distribution()
        assert rec.id == "dist-1"
        assert len(rec.identities) == 1
        assert rec.identities[0].id == "agent-1"

    def test_behavior(self):
        """Behavior stores behavior_key and version_id correctly."""
        beh = _make_behavior()
        assert beh.behavior_key == "default"
        assert beh.version_id == "v1"

    def test_environment(self):
        """Environment stores configuration values and defaults optional fields to None."""
        env = _make_environment()
        assert env.name == "production"
        assert env.configuration_variables["KEY"] == "VALUE"
        assert env.get_configuration_variable("KEY") == "VALUE"
        assert env.get_configuration_variable("MISSING") is None
        assert env.daemon_agent_identity_id is None
        assert env.system_prompt is None

    def test_environment_with_optional_fields(self):
        """Environment stores daemon_agent_identity_id and system_prompt when provided."""
        env = Environment(
            id="env-2",
            name="staging",
            deployment_id="dep-2",
            configuration_variables={},
            daemon_agent_identity_id="daemon-1",
            system_prompt="You are helpful.",
        )
        assert env.daemon_agent_identity_id == "daemon-1"
        assert env.system_prompt == "You are helpful."

    def test_distribution_extension_v1(self):
        """DistributionExtensionV1 defaults version to '1.0.0' and sender_id to None."""
        ext = DistributionExtensionV1(
            distribution=_make_distribution(),
            behavior=_make_behavior(),
            environment=_make_environment(),
        )
        assert ext.version == "1.0.0"
        assert ext.sender_id is None

    def test_distribution_extension_with_sender_id(self):
        """DistributionExtensionV1 stores sender_id when provided."""
        ext = DistributionExtensionV1(
            sender_id="user-xyz",
            distribution=_make_distribution(),
            behavior=_make_behavior(),
            environment=_make_environment(),
        )
        assert ext.sender_id == "user-xyz"

    def test_discriminated_union_principal(self):
        """PrincipalIdentity parsed via discriminated union on 'kind'."""
        rec = _make_distribution()
        identity = rec.identities[0]
        assert isinstance(identity, PrincipalIdentity)

    def test_discriminated_union_service(self):
        """ServiceIdentity is resolved via the discriminated union when kind is 'service'."""
        svc_identity = ServiceIdentity(
            kind="service",
            id="s1",
            network_type="webhook",
            organization_id="org-1",
        )
        rec = Distribution(
            id="d1",
            endpoint_type="webhook",
            url="https://x.com",
            identities=[svc_identity],
        )
        assert isinstance(rec.identities[0], ServiceIdentity)

    def test_json_round_trip(self):
        """DistributionExtensionV1 survives a model_dump / model_validate round-trip."""
        ext = DistributionExtensionV1(
            distribution=_make_distribution(),
            behavior=_make_behavior(),
            environment=_make_environment(),
        )
        data = ext.model_dump()
        restored = DistributionExtensionV1.model_validate(data)
        assert restored.distribution.id == "dist-1"
        assert restored.behavior.behavior_key == "default"


class TestRequestParams:
    def test_get_context_params_required_context_id(self):
        """GetContextParams with only context_id has None pagination fields."""
        p = GetContextParams(context_id="ctx-1")
        assert p.context_id == "ctx-1"
        assert p.history_length is None
        assert p.history_offset is None
        assert p.metadata is None

    def test_get_context_params_with_pagination(self):
        """GetContextParams stores history_length and history_offset when provided."""
        p = GetContextParams(context_id="ctx-1", history_length=10, history_offset=5)
        assert p.history_length == 10
        assert p.history_offset == 5

    def test_get_context_params_missing_context_id_raises(self):
        """GetContextParams raises an exception when context_id is omitted."""
        with pytest.raises(Exception):
            GetContextParams()

    def test_get_contexts_list_params_all_optional(self):
        """GetContextsListParams constructed with no args has None pagination fields."""
        p = GetContextsListParams()
        assert p.history_length is None
        assert p.history_offset is None

    def test_get_contexts_list_params_with_values(self):
        """GetContextsListParams stores history_length when provided."""
        p = GetContextsListParams(history_length=20, history_offset=0)
        assert p.history_length == 20


class TestRequestModels:
    def test_get_context_request_defaults(self):
        """GetContextRequest defaults jsonrpc to '2.0' and method to 'GetContext'."""
        req = GetContextRequest(
            id=1,
            params=GetContextParams(context_id="ctx-1"),
        )
        assert req.jsonrpc == "2.0"
        assert req.method == "GetContext"
        assert req.id == 1

    def test_get_context_request_string_id(self):
        """GetContextRequest accepts a string id."""
        req = GetContextRequest(
            id="req-abc",
            params=GetContextParams(context_id="ctx-1"),
        )
        assert req.id == "req-abc"

    def test_get_contexts_list_request_defaults(self):
        """GetContextsListRequest defaults jsonrpc to '2.0' and method to 'GetContexts'."""
        req = GetContextsListRequest(
            id=42,
            params=GetContextsListParams(),
        )
        assert req.jsonrpc == "2.0"
        assert req.method == "GetContexts"

    def test_get_context_request_json_serialization(self):
        """GetContextRequest serializes to JSON with correct method and jsonrpc fields."""
        req = GetContextRequest(
            id=1,
            params=GetContextParams(context_id="ctx-1"),
        )
        data = json.loads(req.model_dump_json())
        assert data["method"] == "GetContext"
        assert data["jsonrpc"] == "2.0"


class TestResponseModels:
    def _conversation(self) -> Conversation:
        return Conversation(
            context_id="ctx-1",
            status=ConversationTaskStatus(state=TaskState.TASK_STATE_WORKING),
        )

    def test_get_context_success_response(self):
        """GetContextSuccessResponse defaults jsonrpc to '2.0' and id to None."""
        resp = GetContextSuccessResponse(result=self._conversation())
        assert resp.jsonrpc == "2.0"
        assert resp.id is None
        assert resp.result.context_id == "ctx-1"

    def test_get_context_success_response_with_id(self):
        """GetContextSuccessResponse stores id when provided."""
        resp = GetContextSuccessResponse(id=10, result=self._conversation())
        assert resp.id == 10

    def test_get_contexts_list_success_response(self):
        """GetContextsListSuccessResponse stores the ContextsList result."""
        cl = ContextsList(root=["ctx-1", "ctx-2"])
        resp = GetContextsListSuccessResponse(result=cl)
        assert resp.jsonrpc == "2.0"
        assert resp.result.root == ["ctx-1", "ctx-2"]

    def test_get_context_success_response_json(self):
        """GetContextSuccessResponse serializes result with camelCase aliases."""
        resp = GetContextSuccessResponse(id="r1", result=self._conversation())
        data = json.loads(resp.model_dump_json())
        assert data["jsonrpc"] == "2.0"
        # A2ABaseModel serializes with camelCase aliases
        assert data["result"]["contextId"] == "ctx-1"

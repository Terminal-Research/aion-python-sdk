from unittest.mock import Mock, patch

import pytest
from a2a.types import Artifact, Part, Role, TaskArtifactUpdateEvent, TaskState, TaskStatusUpdateEvent
from aion.langgraph.events.custom_events import (
    ArtifactCustomEvent,
    MessageCustomEvent,
    ReactionCustomEvent,
    TaskUpdateCustomEvent,
)
from aion.shared.agent.adapters import InterruptInfo
from aion.shared.constants import MESSAGING_EXTENSION_URI_V1
from aion.shared.types import ArtifactId
from aion.shared.types.a2a.extensions.messaging import MessageActionPayload, ReactionActionPayload
from langchain_core.messages import AIMessage, HumanMessage

from aion.server_langgraph.execution.event_converter import LangGraphA2AConverter

from .helpers import make_ai_message, make_interrupt_info, make_mock_chunk

LC_CONVERTER_PATH = "aion.server_langgraph.execution.event_converter.LcToA2AConverter.from_message"


class TestConvert:
    """Routing logic in the top-level convert() dispatcher."""

    def test_values_event_returns_empty_list(self, converter):
        """'values' is a skipped event type and produces no A2A events."""
        assert converter.convert("values", {}) == []

    def test_updates_event_returns_empty_list(self, converter):
        """'updates' is a skipped event type and produces no A2A events."""
        assert converter.convert("updates", {}) == []

    def test_messages_event_produces_a2a_output(self, converter):
        """'messages' event reaches _convert_message and produces A2A events."""
        msg = make_ai_message("hi")
        with patch(LC_CONVERTER_PATH, return_value=[Part(text="hi")]):
            result = converter.convert("messages", msg)
        assert len(result) == 1
        assert isinstance(result[0], TaskStatusUpdateEvent)

    def test_unknown_event_type_returns_empty_list(self, converter):
        """Unknown event types produce no events."""
        assert converter.convert("unknown_type", {}) == []


class TestConvertStreamingChunk:
    """AIMessageChunk streaming logic, append-flag tracking, and empty-chunk handling."""

    def test_first_chunk_has_append_false(self, converter):
        """The first chunk must open the artifact with append=False."""
        chunk = make_mock_chunk()
        with patch(LC_CONVERTER_PATH, return_value=[Part(text="hello")]):
            events = converter._convert_streaming_chunk(chunk)
        assert len(events) == 1
        assert events[0].append is False
        assert events[0].artifact.artifact_id == ArtifactId.STREAM_DELTA.value

    def test_second_chunk_has_append_true(self, converter):
        """After the first chunk, subsequent chunks use append=True."""
        chunk = make_mock_chunk()
        with patch(LC_CONVERTER_PATH, return_value=[Part(text="a")]):
            converter._convert_streaming_chunk(chunk)
        with patch(LC_CONVERTER_PATH, return_value=[Part(text="b")]):
            events = converter._convert_streaming_chunk(chunk)
        assert events[0].append is True

    def test_empty_intermediate_chunk_is_skipped(self, converter):
        """Empty chunk that is not the last one produces no events."""
        chunk = make_mock_chunk(chunk_position=None)
        with patch(LC_CONVERTER_PATH, return_value=[]):
            events = converter._convert_streaming_chunk(chunk)
        assert events == []

    def test_empty_last_chunk_is_emitted(self, converter):
        """Empty last chunk (chunk_position='last') is still emitted to close the stream."""
        chunk = make_mock_chunk(chunk_position="last")
        with patch(LC_CONVERTER_PATH, return_value=[]):
            events = converter._convert_streaming_chunk(chunk)
        assert len(events) == 1
        assert events[0].last_chunk is True


class TestConvertFullMessage:
    """Complete AIMessage conversion to TaskStatusUpdateEvent."""

    def test_ai_message_with_parts_produces_working_event(self, converter):
        """AIMessage with non-empty parts yields a TaskStatusUpdateEvent(state=WORKING)."""
        msg = make_ai_message("Hello")
        with patch(LC_CONVERTER_PATH, return_value=[Part(text="Hello")]):
            events = converter._convert_full_message(msg)
        assert len(events) == 1
        assert events[0].status.state == TaskState.TASK_STATE_WORKING

    def test_ai_message_without_parts_returns_empty(self, converter):
        """AIMessage that converts to zero parts produces no events."""
        msg = make_ai_message()
        with patch(LC_CONVERTER_PATH, return_value=[]):
            events = converter._convert_full_message(msg)
        assert events == []

    def test_ai_message_role_is_agent(self, converter):
        """AIMessage is mapped to ROLE_AGENT."""
        msg = make_ai_message("Hi")
        with patch(LC_CONVERTER_PATH, return_value=[Part(text="Hi")]):
            events = converter._convert_full_message(msg)
        assert events[0].status.message.role == Role.ROLE_AGENT

    def test_human_message_role_is_user(self, converter):
        """HumanMessage is mapped to ROLE_USER."""
        msg = HumanMessage(content="Hi")
        with patch(LC_CONVERTER_PATH, return_value=[Part(text="Hi")]):
            events = converter._convert_full_message(msg)
        assert events[0].status.message.role == Role.ROLE_USER

    def test_message_id_from_message_is_preserved(self, converter):
        """If the source message has an id, it is used as message_id."""
        msg = make_ai_message("Hi", id="existing-id")
        with patch(LC_CONVERTER_PATH, return_value=[Part(text="Hi")]):
            events = converter._convert_full_message(msg)
        assert events[0].status.message.message_id == "existing-id"

    def test_message_id_generated_as_uuid_when_absent(self, converter):
        """If the source message has no id, a UUID-formatted string is generated."""
        msg = make_ai_message("Hi", id=None)
        with patch(LC_CONVERTER_PATH, return_value=[Part(text="Hi")]):
            events = converter._convert_full_message(msg)
        message_id = events[0].status.message.message_id
        assert len(message_id) == 36
        assert message_id.count("-") == 4


class TestConvertCustom:
    """Dispatch and output for all supported custom event types."""

    def test_artifact_event_produces_artifact_update(self, converter):
        """ArtifactCustomEvent passes its artifact through as TaskArtifactUpdateEvent."""
        artifact = Artifact(artifact_id="test-id", name="Test", parts=[])
        event = ArtifactCustomEvent(artifact=artifact, append=False, is_last_chunk=True)
        events = converter._convert_custom(event)
        assert len(events) == 1
        assert isinstance(events[0], TaskArtifactUpdateEvent)
        assert events[0].artifact.artifact_id == "test-id"

    def test_artifact_event_preserves_append_flag(self, converter):
        """ArtifactCustomEvent.append is forwarded to TaskArtifactUpdateEvent."""
        artifact = Artifact(artifact_id="id", name="n", parts=[])
        event = ArtifactCustomEvent(artifact=artifact, append=True, is_last_chunk=False)
        events = converter._convert_custom(event)
        assert events[0].append is True
        assert events[0].last_chunk is False

    def test_ephemeral_message_produces_ephemeral_artifact(self, converter):
        """MessageCustomEvent with ephemeral=True produces an EPHEMERAL_MESSAGE artifact."""
        msg = make_ai_message("thinking...")
        event = MessageCustomEvent(message=msg, ephemeral=True)
        with patch(LC_CONVERTER_PATH, return_value=[Part(text="thinking...")]):
            events = converter._convert_custom(event)
        assert len(events) == 1
        assert isinstance(events[0], TaskArtifactUpdateEvent)
        assert events[0].artifact.artifact_id == ArtifactId.EPHEMERAL_MESSAGE.value
        assert events[0].append is False
        assert events[0].last_chunk is True

    def test_message_event_with_routing_adds_extension_part(self, converter):
        """MessageCustomEvent with routing appends MessageActionPayload as an extension Part."""
        msg = make_ai_message("Hi")
        routing = MessageActionPayload(trajectory="direct-message", context_id="ctx-x")
        event = MessageCustomEvent(message=msg, routing=routing)
        with patch(LC_CONVERTER_PATH, return_value=[Part(text="Hi")]):
            events = converter._convert_custom(event)
        assert len(events) == 1
        result_msg = events[0].status.message
        # original text part + routing extension part
        assert len(result_msg.parts) == 2
        assert MESSAGING_EXTENSION_URI_V1 in result_msg.extensions

    def test_plain_message_event_produces_working_status_update(self, converter):
        """MessageCustomEvent without routing/ephemeral yields TaskStatusUpdateEvent(WORKING)."""
        msg = make_ai_message("Response")
        event = MessageCustomEvent(message=msg)
        with patch(LC_CONVERTER_PATH, return_value=[Part(text="Response")]):
            events = converter._convert_custom(event)
        assert len(events) == 1
        assert isinstance(events[0], TaskStatusUpdateEvent)
        assert events[0].status.state == TaskState.TASK_STATE_WORKING

    def test_reaction_event_produces_message_with_extension_part(self, converter):
        """ReactionCustomEvent produces a TaskStatusUpdateEvent with a single DataPart."""
        payload = ReactionActionPayload(
            context_id="ctx-1", message_id="msg-1", reaction_key="thumbsup", operation="add"
        )
        event = ReactionCustomEvent(payload=payload)
        events = converter._convert_custom(event)
        assert len(events) == 1
        result_msg = events[0].status.message
        assert len(result_msg.parts) == 1
        assert MESSAGING_EXTENSION_URI_V1 in result_msg.extensions
        assert result_msg.role == Role.ROLE_AGENT

    def test_task_update_event_delegates_to_convert_task_update(self, converter):
        """TaskUpdateCustomEvent routes to _convert_task_update."""
        event = TaskUpdateCustomEvent(metadata={"key": "val"})
        with patch.object(converter, "_convert_task_update", return_value=[Mock()]) as mock_tu:
            converter._convert_custom(event)
            mock_tu.assert_called_once_with(event)

    def test_unknown_custom_type_returns_empty(self, converter):
        """Unknown custom event type produces no events."""
        assert converter._convert_custom(object()) == []


class TestConvertTaskUpdate:
    """_convert_task_update filtering and combination logic."""

    def test_message_only_produces_status_update_with_message(self, converter):
        """TaskUpdateCustomEvent with only a message produces event with status.message set."""
        msg = make_ai_message("Update")
        event = TaskUpdateCustomEvent(message=msg)
        with patch(LC_CONVERTER_PATH, return_value=[Part(text="Update")]):
            events = converter._convert_task_update(event)
        assert len(events) == 1
        assert events[0].status.message is not None

    def test_metadata_only_produces_status_update_with_metadata(self, converter):
        """TaskUpdateCustomEvent with public metadata produces event with metadata set."""
        event = TaskUpdateCustomEvent(metadata={"progress": "50%"})
        events = converter._convert_task_update(event)
        assert len(events) == 1
        assert events[0].metadata == {"progress": "50%"}

    def test_aion_prefixed_keys_are_filtered_out(self, converter):
        """Keys starting with 'aion:' are stripped before forwarding metadata."""
        event = TaskUpdateCustomEvent(metadata={"aion:internal": "x", "public": "y"})
        events = converter._convert_task_update(event)
        assert "aion:internal" not in events[0].metadata
        assert events[0].metadata["public"] == "y"

    def test_all_aion_keys_with_no_message_returns_empty(self, converter):
        """When all metadata keys are aion:-prefixed and there's no message, returns []."""
        event = TaskUpdateCustomEvent(metadata={"aion:a": "1", "aion:b": "2"})
        assert converter._convert_task_update(event) == []

    def test_neither_message_nor_public_metadata_returns_empty(self, converter):
        """No message and no public metadata yields an empty list."""
        assert converter._convert_task_update(TaskUpdateCustomEvent()) == []


class TestTerminalEvents:
    """convert_interrupt, convert_complete, and convert_error produce correct terminal statuses."""

    def test_convert_interrupt_no_interrupts_produces_input_required_without_message(self, converter):
        """Empty interrupt list yields INPUT_REQUIRED with no message set."""
        event = converter.convert_interrupt([])
        assert event.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        assert not event.status.HasField("message")

    def test_convert_interrupt_with_info_includes_message(self, converter):
        """Single InterruptInfo produces INPUT_REQUIRED with a message."""
        info = make_interrupt_info(id="i-1", value="Need input", prompt="Please answer:")
        event = converter.convert_interrupt([info])
        assert event.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        assert "Please answer:" in event.status.message.parts[0].text

    def test_convert_interrupt_embeds_interrupt_id_in_metadata(self, converter):
        """interrupt_id from InterruptInfo is embedded in message metadata."""
        info = make_interrupt_info(id="my-interrupt-id")
        event = converter.convert_interrupt([info])
        assert event.status.message.metadata["interruptId"] == "my-interrupt-id"

    def test_convert_complete_returns_completed_status(self, converter):
        """convert_complete() produces state=COMPLETED."""
        assert converter.convert_complete().status.state == TaskState.TASK_STATE_COMPLETED

    def test_convert_error_returns_failed_status(self, converter):
        """convert_error() produces state=FAILED regardless of error details."""
        assert converter.convert_error("boom", "RuntimeError").status.state == TaskState.TASK_STATE_FAILED

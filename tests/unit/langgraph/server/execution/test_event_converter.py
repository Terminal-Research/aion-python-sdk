from unittest.mock import Mock, patch

import pytest
from a2a.types import Artifact, Part, Role, TaskArtifactUpdateEvent, TaskState, TaskStatusUpdateEvent
from aion.langgraph.authoring.events.custom_events import (
    ArtifactCustomEvent,
    MessageCustomEvent,
    ReactionCustomEvent,
)
from aion.core.constants import MESSAGING_EXTENSION_URI_V1
from aion.core.a2a import ArtifactId
from aion.core.a2a.extensions.messaging import MessageActionPayload, ReactionActionPayload
from langchain_core.messages import AIMessage, HumanMessage

from aion.langgraph.server.execution.event_converter import LangGraphA2AConverter

from ..helpers import make_ai_message, make_interrupt, make_mock_chunk

LC_CONVERTER_PATH = "aion.langgraph.server.execution.event_converter.LcToA2AConverter.from_message"


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
        assert result[0].status.state == TaskState.TASK_STATE_WORKING

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

    def test_empty_last_chunk_produces_no_event(self, converter):
        """A chunk_position='last' chunk without content yields nothing: every update carries a Part."""
        chunk = make_mock_chunk(chunk_position="last")
        with patch(LC_CONVERTER_PATH, return_value=[]):
            events = converter._convert_streaming_chunk(chunk)
        assert events == []

    def test_content_last_chunk_carries_last_chunk_flag(self, converter):
        """A content-bearing chunk the provider marked as last keeps last_chunk=True."""
        chunk = make_mock_chunk(chunk_position="last")
        with patch(LC_CONVERTER_PATH, return_value=[Part(text="World!")]):
            events = converter._convert_streaming_chunk(chunk)
        assert len(events) == 1
        assert events[0].last_chunk is True
        assert [p.text for p in events[0].artifact.parts] == ["World!"]


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

    def test_reaction_event_produces_artifact_with_reaction_id(self, converter):
        """ReactionCustomEvent produces a TaskArtifactUpdateEvent with REACTION artifact_id."""
        payload = ReactionActionPayload(
            context_id="ctx-1", message_id="msg-1", reaction_key="thumbsup", operation="add"
        )
        event = ReactionCustomEvent(payload=payload)
        events = converter._convert_custom(event)
        assert len(events) == 1
        assert isinstance(events[0], TaskArtifactUpdateEvent)
        artifact = events[0].artifact
        assert artifact.artifact_id == ArtifactId.REACTION.value
        assert len(artifact.parts) == 1
        assert artifact.parts[0].data is not None

    def test_unknown_custom_type_returns_empty(self, converter):
        """Unknown custom event type produces no events."""
        assert converter._convert_custom(object()) == []


class TestTerminalEvents:
    """convert_interrupt, convert_complete, and convert_error produce correct terminal statuses."""

    def test_convert_interrupt_no_interrupts_produces_input_required_without_message(self, converter):
        """Empty interrupt list yields INPUT_REQUIRED with no message set."""
        event = converter.convert_interrupt([])
        assert event.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        assert not event.status.HasField("message")

    def test_convert_interrupt_with_an_interrupt_includes_message(self, converter):
        """A pending interrupt produces INPUT_REQUIRED with its question as the message."""
        event = converter.convert_interrupt((make_interrupt(id="i-1", value="Please answer:"),))
        assert event.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        assert event.status.message.parts[0].text == "Please answer:"

    def test_convert_interrupt_embeds_interrupt_id_in_metadata(self, converter):
        """The interrupt's id is embedded in message metadata."""
        event = converter.convert_interrupt([make_interrupt(id="my-interrupt-id")])
        assert event.status.message.metadata["interruptId"] == "my-interrupt-id"

    def test_convert_interrupt_asks_the_first_of_several(self, converter):
        """Only the first pending interrupt becomes the message."""
        event = converter.convert_interrupt([
            make_interrupt(id="first", value="First?"),
            make_interrupt(id="second", value="Second?"),
        ])
        assert event.status.message.parts[0].text == "First?"
        assert event.status.message.metadata["interruptId"] == "first"

    @pytest.mark.parametrize(
        ("value", "prompt"),
        [
            pytest.param("Please confirm the action.", "Please confirm the action.", id="string-value"),
            pytest.param({"prompt": "What is your name?", "options": ["a"]}, "What is your name?", id="dict-prompt"),
            pytest.param({"type": "approval", "choices": ["yes", "no"]}, "Agent requires input", id="dict-without-prompt"),
            pytest.param({"prompt": ""}, "Agent requires input", id="dict-empty-prompt"),
            pytest.param(42, "Agent requires input", id="other-value"),
        ],
    )
    def test_convert_interrupt_prompt_text(self, converter, value, prompt):
        """The question is a string value, else a dict's "prompt", else a generic request."""
        event = converter.convert_interrupt([make_interrupt(value=value)])
        assert event.status.message.parts[0].text == prompt

    def test_convert_complete_returns_completed_status(self, converter):
        """convert_complete() produces state=COMPLETED."""
        assert converter.convert_complete().status.state == TaskState.TASK_STATE_COMPLETED

    def test_convert_error_returns_failed_status(self, converter):
        """convert_error() produces state=FAILED regardless of error details."""
        assert converter.convert_error("boom", "RuntimeError").status.state == TaskState.TASK_STATE_FAILED

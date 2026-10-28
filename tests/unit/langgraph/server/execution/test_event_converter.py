from unittest.mock import Mock, patch

import pytest
from a2a.types import Artifact, Part, Role, TaskArtifactUpdateEvent, TaskState, TaskStatusUpdateEvent
from aion.langgraph.authoring.events.custom_events import (
    ArtifactCustomEvent,
    MessageCustomEvent,
    ReactionCustomEvent,
    ThinkingCustomEvent,
)
from aion.core.constants import MESSAGING_EXTENSION_URI_V1
from aion.core.a2a import ArtifactId
from aion.core.a2a.extensions.messaging import MessageActionPayload, ReactionActionPayload
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

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

    def test_an_empty_last_chunk_without_an_open_section_produces_nothing(self, converter):
        """A chunk_position='last' chunk without content ends nothing when nothing streamed."""
        chunk = make_mock_chunk(chunk_position="last")
        with patch(LC_CONVERTER_PATH, return_value=[]):
            events = converter._convert_streaming_chunk(chunk)
        assert events == []

    def test_an_empty_last_chunk_is_the_sections_end_mark(self, converter):
        """langchain-core usually ends a stream with an empty chunk marked last: it ends the section."""
        with patch(LC_CONVERTER_PATH, return_value=[Part(text="Hello")]):
            converter._convert_streaming_chunk(make_mock_chunk())
        with patch(LC_CONVERTER_PATH, return_value=[]):
            events = converter._convert_streaming_chunk(make_mock_chunk(chunk_position="last"))
        assert [([p.text for p in e.artifact.parts], e.append, e.last_chunk) for e in events] == [([""], True, True)]
        assert converter.close_stream() == []

    def test_a_content_last_chunk_ends_the_section_itself(self, converter):
        """A content-bearing chunk the provider marked as last carries lastChunk; no end mark follows."""
        with patch(LC_CONVERTER_PATH, return_value=[Part(text="Hello, ")]):
            converter._convert_streaming_chunk(make_mock_chunk())
        with patch(LC_CONVERTER_PATH, return_value=[Part(text="World!")]):
            events = converter._convert_streaming_chunk(make_mock_chunk(chunk_position="last"))
        assert [(e.append, e.last_chunk) for e in events] == [(True, True)]
        assert [p.text for p in events[0].artifact.parts] == ["World!"]
        assert converter.close_stream() == []

    def test_a_single_chunk_marked_last_starts_and_ends_its_section(self, converter):
        """append=False starts the artifact over whatever lastChunk says."""
        with patch(LC_CONVERTER_PATH, return_value=[Part(text="Hi")]):
            events = converter._convert_streaming_chunk(make_mock_chunk(chunk_position="last"))
        assert [(e.append, e.last_chunk) for e in events] == [(False, True)]

    def test_chunk_carries_only_the_agents_metadata(self, converter):
        """A chunk's artifact carries the agent's metadata, without platform keys or a streaming status."""
        chunk = make_mock_chunk()
        with patch(LC_CONVERTER_PATH, return_value=[Part(text="a")]):
            events = converter._convert_streaming_chunk(chunk, metadata={"lane": "weather", "aion:network": "x"})
        assert dict(events[0].artifact.metadata) == {"lane": "weather"}


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

    def test_thinking_event_produces_one_part_of_the_thinking_artifact(self, converter):
        """ThinkingCustomEvent produces a THINKING_DELTA artifact update with the thought as its one text part."""
        events = converter._convert_custom(ThinkingCustomEvent(text="Looking up the account"))
        assert len(events) == 1
        assert isinstance(events[0], TaskArtifactUpdateEvent)
        assert events[0].artifact.artifact_id == ArtifactId.THINKING_DELTA.value
        assert [part.text for part in events[0].artifact.parts] == ["Looking up the account"]
        assert events[0].append is True
        assert events[0].last_chunk is False

    def test_thinking_event_keeps_only_the_agents_metadata(self, converter):
        """Platform keys are dropped from a thought's metadata; the agent's own keys stay."""
        event = ThinkingCustomEvent(text="Looking up", metadata={"step": "lookup", "aion:network": "x"})
        events = converter._convert_custom(event)
        assert dict(events[0].artifact.metadata) == {"step": "lookup"}

    def test_thinking_event_without_text_produces_nothing(self, converter):
        assert converter._convert_custom(ThinkingCustomEvent(text="")) == []

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
        """MessageCustomEvent without routing yields TaskStatusUpdateEvent(WORKING)."""
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


class TestRouting:
    """A routing target travels as a data part beside the content it routes, in a message or an artifact."""

    ROUTING = MessageActionPayload(trajectory="reply", context_id="C1", reply_to_message_id="m-1")

    @staticmethod
    def _contents(event) -> list:
        return [part.text for part in event.artifact.parts if not MessageActionPayload.is_part(part)]

    @staticmethod
    def _routed(event) -> bool:
        return any(MessageActionPayload.is_part(part) for part in event.artifact.parts)

    def test_a_routed_artifact_carries_its_target_as_a_part(self, converter):
        artifact = Artifact(artifact_id="report", name="Report", parts=[Part(text="all good")])

        [event] = converter._convert_custom(ArtifactCustomEvent(artifact=artifact, routing=self.ROUTING))

        assert self._contents(event) == ["all good"]
        assert MessageActionPayload.is_part(event.artifact.parts[-1])
        assert list(event.artifact.extensions) == [MESSAGING_EXTENSION_URI_V1]
        assert MESSAGING_EXTENSION_URI_V1 not in event.metadata
        assert len(artifact.parts) == 1

    def test_a_routed_section_carries_its_target_on_its_first_chunk_only(self, converter):
        events = []
        for text in ("one ", "two"):
            events += converter.convert(
                "custom", MessageCustomEvent(message=AIMessageChunk(content=text), routing=self.ROUTING)
            )
        events += converter.convert(
            "custom", MessageCustomEvent(message=AIMessage(content="one two", id="reply-1"), routing=self.ROUTING)
        )

        first, second, final = events
        assert (self._contents(first), self._routed(first)) == (["one "], True)
        assert list(first.artifact.extensions) == [MESSAGING_EXTENSION_URI_V1]
        assert (self._contents(second), self._routed(second)) == (["two"], False)
        assert (self._contents(final), self._routed(final)) == ([""], False)
        assert (final.append, final.last_chunk) == (True, True)

    def test_a_routed_section_the_run_left_open_ends_with_the_end_mark(self, converter):
        converter.convert("custom", MessageCustomEvent(message=AIMessageChunk(content="one"), routing=self.ROUTING))

        [final] = converter.close_stream()

        assert (self._contents(final), self._routed(final)) == ([""], False)
        assert final.last_chunk is True

    def test_the_next_section_starts_without_the_last_ones_target(self, converter):
        converter.convert("custom", MessageCustomEvent(message=AIMessageChunk(content="one"), routing=self.ROUTING))
        converter.close_stream()

        [chunk] = converter.convert("messages", AIMessageChunk(content="model text", id="run-2"))
        [final] = converter.close_stream()

        assert not self._routed(chunk)
        assert not self._routed(final)


class TestCloseStream:
    """close_stream() ends a section the run left open, with the text of its chunks."""

    def test_open_graph_section_ends_with_the_end_mark(self, converter):
        converter.convert("messages", AIMessageChunk(content="It is ", id="run-1"))
        converter.convert("messages", AIMessageChunk(content="sunny.", id="run-1"))

        events = converter.close_stream()

        assert len(events) == 1
        end = events[0]
        assert end.artifact.artifact_id == ArtifactId.STREAM_DELTA.value
        assert [part.text for part in end.artifact.parts] == [""]
        assert (end.append, end.last_chunk) == (True, True)
        assert not end.artifact.HasField("metadata")

    def test_open_thread_section_ends_with_the_end_mark(self, converter):
        converter.convert("custom", MessageCustomEvent(message=AIMessageChunk(content="one "), metadata={"lane": "a"}))
        converter.convert("custom", MessageCustomEvent(message=AIMessageChunk(content="two"), metadata={"lane": "a"}))

        events = converter.close_stream()

        assert [([part.text for part in e.artifact.parts], e.last_chunk) for e in events] == [([""], True)]

    def test_nothing_open_produces_nothing(self, converter):
        assert converter.close_stream() == []

    def test_a_closed_section_is_not_closed_twice(self, converter):
        converter.convert("messages", AIMessageChunk(content="It is sunny.", id="run-1"))
        converter.close_stream()

        assert converter.close_stream() == []

    def test_the_next_chunk_starts_a_new_section(self, converter):
        converter.convert("messages", AIMessageChunk(content="It is sunny.", id="run-1"))
        converter.close_stream()

        events = converter.convert("messages", AIMessageChunk(content="Anything else?", id="run-1"))

        assert events[0].append is False


class TestTerminalEvents:
    """convert_interrupt and convert_complete produce correct terminal statuses."""

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

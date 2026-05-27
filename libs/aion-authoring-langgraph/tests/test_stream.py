import pytest
from unittest.mock import MagicMock
from langchain_core.messages import AIMessage, AIMessageChunk

from aion.langgraph.authoring.stream import (
    emit_file_artifact,
    emit_data_artifact,
    emit_message,
    emit_task_update,
    emit_reaction,
)
from aion.langgraph.authoring.events.custom_events import (
    MessageCustomEvent,
    ReactionCustomEvent,
    TaskUpdateCustomEvent,
)
from aion.core.types.a2a.extensions.messaging import ReactionActionPayload


@pytest.fixture
def writer():
    return MagicMock()


class TestEmitFileArtifact:
    def test_url_stored_in_part(self, writer):
        # URL is actually placed inside the artifact Part, not just wrapped in an event
        emit_file_artifact(writer, url="https://example.com/file.pdf", mime_type="application/pdf")
        writer.assert_called_once()
        part = writer.call_args[0][0].artifact.parts[0]
        assert part.url == "https://example.com/file.pdf"

    def test_raw_bytes_stored_in_part(self, writer):
        # bytes content is actually placed inside the artifact Part
        emit_file_artifact(writer, data=b"binary content", mime_type="image/png")
        writer.assert_called_once()
        part = writer.call_args[0][0].artifact.parts[0]
        assert part.raw == b"binary content"

    def test_custom_name(self, writer):
        # explicit name is passed through to the artifact
        emit_file_artifact(writer, url="https://example.com/file.pdf", mime_type="application/pdf", name="my_report")
        assert writer.call_args[0][0].artifact.name == "my_report"

    def test_raises_when_neither_url_nor_data(self, writer):
        # omitting both url and data is an error
        with pytest.raises(ValueError):
            emit_file_artifact(writer, mime_type="application/pdf")

    def test_raises_when_both_url_and_data(self, writer):
        # providing both url and data is an error
        with pytest.raises(ValueError):
            emit_file_artifact(writer, url="https://example.com/f.pdf", data=b"bytes", mime_type="application/pdf")

    def test_raises_type_error_when_data_not_bytes(self, writer):
        # data must be bytes, not str or other types
        with pytest.raises(TypeError):
            emit_file_artifact(writer, data="not bytes", mime_type="text/plain")

    def test_explicit_artifact_id_used(self, writer):
        # caller-supplied artifact_id is preserved as-is
        emit_file_artifact(writer, url="https://example.com/f.pdf", mime_type="application/pdf", artifact_id="my-id")
        assert writer.call_args[0][0].artifact.artifact_id == "my-id"

    def test_append_and_is_last_chunk_passed_through(self, writer):
        # streaming flags are forwarded to the event unchanged
        emit_file_artifact(writer, url="https://x.com/f.pdf", mime_type="application/pdf", append=True, is_last_chunk=False)
        event = writer.call_args[0][0]
        assert event.append is True
        assert event.is_last_chunk is False


class TestEmitDataArtifact:
    def test_data_stored_as_proto_value_in_part(self, writer):
        # dict is serialized into a protobuf Value stored in the Part's data field
        emit_data_artifact(writer, {"key": "value"})
        writer.assert_called_once()
        part = writer.call_args[0][0].artifact.parts[0]
        assert part.data is not None

    def test_custom_name(self, writer):
        # explicit name is passed through to the artifact
        emit_data_artifact(writer, {"key": "value"}, name="results")
        assert writer.call_args[0][0].artifact.name == "results"

    def test_explicit_artifact_id(self, writer):
        # caller-supplied artifact_id is preserved
        emit_data_artifact(writer, {"key": "value"}, artifact_id="data-id-123")
        assert writer.call_args[0][0].artifact.artifact_id == "data-id-123"

    def test_append_and_is_last_chunk(self, writer):
        # streaming flags are forwarded to the event unchanged
        emit_data_artifact(writer, {}, append=True, is_last_chunk=False)
        event = writer.call_args[0][0]
        assert event.append is True
        assert event.is_last_chunk is False


class TestEmitMessage:
    def test_emits_message_event_with_aimessage(self, writer):
        # AIMessage produces a MessageCustomEvent with the same message object
        msg = AIMessage(content="Hello")
        emit_message(writer, msg)
        writer.assert_called_once()
        event = writer.call_args[0][0]
        assert isinstance(event, MessageCustomEvent)
        assert event.message is msg

    def test_emits_message_event_with_chunk(self, writer):
        # AIMessageChunk is accepted and wrapped in MessageCustomEvent
        chunk = AIMessageChunk(content="Hello")
        emit_message(writer, chunk)
        event = writer.call_args[0][0]
        assert isinstance(event, MessageCustomEvent)
        assert event.message is chunk

    def test_ephemeral_false_by_default(self, writer):
        # messages are durable (not ephemeral) unless explicitly requested
        emit_message(writer, AIMessage(content="Hi"))
        assert writer.call_args[0][0].ephemeral is False

    def test_ephemeral_true_propagates(self, writer):
        # ephemeral=True is forwarded to the event
        emit_message(writer, AIMessage(content="Hi"), ephemeral=True)
        assert writer.call_args[0][0].ephemeral is True

    def test_routing_propagates(self, writer):
        # explicit routing target is attached to the event
        from aion.core.types.a2a.extensions.messaging import MessageActionPayload
        routing = MessageActionPayload(trajectory="conversation", context_id="C1")
        emit_message(writer, AIMessage(content="Hi"), routing=routing)
        assert writer.call_args[0][0].routing == routing

    def test_routing_none_by_default(self, writer):
        # routing is None when not specified (uses distribution default)
        emit_message(writer, AIMessage(content="Hi"))
        assert writer.call_args[0][0].routing is None


class TestEmitTaskUpdate:
    def test_raises_when_neither_message_nor_metadata(self, writer):
        # at least one of message or metadata must be provided
        with pytest.raises(ValueError):
            emit_task_update(writer)

    def test_emits_with_message_only(self, writer):
        # message-only update: metadata is None in the event
        msg = AIMessage(content="Done")
        emit_task_update(writer, message=msg)
        writer.assert_called_once()
        event = writer.call_args[0][0]
        assert isinstance(event, TaskUpdateCustomEvent)
        assert event.message is msg
        assert event.metadata is None

    def test_emits_with_metadata_only(self, writer):
        # metadata-only update: message is None in the event
        emit_task_update(writer, metadata={"progress": 50})
        event = writer.call_args[0][0]
        assert isinstance(event, TaskUpdateCustomEvent)
        assert event.message is None
        assert event.metadata == {"progress": 50}

    def test_emits_with_both(self, writer):
        # message and metadata together produce a single combined event
        msg = AIMessage(content="Done")
        emit_task_update(writer, message=msg, metadata={"step": "done"})
        writer.assert_called_once()
        event = writer.call_args[0][0]
        assert event.message is msg
        assert event.metadata == {"step": "done"}


class TestEmitReaction:
    def test_emits_reaction_event_with_payload(self, writer):
        # payload is wrapped in ReactionCustomEvent and all fields are preserved
        payload = ReactionActionPayload(
            context_id="C123",
            message_id="msg1",
            reaction_key="thumbsup",
            operation="add",
        )
        emit_reaction(writer, payload)
        writer.assert_called_once()
        event = writer.call_args[0][0]
        assert isinstance(event, ReactionCustomEvent)
        assert event.payload is payload

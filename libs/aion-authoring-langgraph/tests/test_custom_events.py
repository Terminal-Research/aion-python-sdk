import pytest
from pydantic import ValidationError
from langchain_core.messages import AIMessage, AIMessageChunk
from a2a.types import Artifact

from aion.langgraph.authoring.events.custom_events import (
    ArtifactCustomEvent,
    MessageCustomEvent,
    ReactionCustomEvent,
    TaskUpdateCustomEvent,
)
from aion.core.types.a2a.extensions.messaging import ReactionActionPayload


class TestArtifactCustomEvent:
    def test_valid_construction_with_defaults(self):
        # append=False and is_last_chunk=True are the safe defaults for single-shot emission
        artifact = Artifact(artifact_id="a1", name="test", parts=[])
        event = ArtifactCustomEvent(artifact=artifact)
        assert event.append is False
        assert event.is_last_chunk is True

    def test_streaming_flags_can_be_overridden(self):
        # append and is_last_chunk can be set for chunked streaming
        artifact = Artifact(artifact_id="a1", name="test", parts=[])
        event = ArtifactCustomEvent(artifact=artifact, append=True, is_last_chunk=False)
        assert event.append is True
        assert event.is_last_chunk is False

    def test_missing_artifact_raises(self):
        # artifact is a required field
        with pytest.raises(ValidationError):
            ArtifactCustomEvent()


class TestMessageCustomEvent:
    def test_valid_with_aimessage(self):
        # AIMessage is accepted; ephemeral and routing default to safe values
        msg = AIMessage(content="Hello")
        event = MessageCustomEvent(message=msg)
        assert event.message is msg
        assert event.ephemeral is False
        assert event.routing is None

    def test_valid_with_chunk(self):
        # AIMessageChunk is accepted (union type — both are valid)
        chunk = AIMessageChunk(content="chunk")
        event = MessageCustomEvent(message=chunk)
        assert event.message is chunk

    def test_ephemeral_flag(self):
        # ephemeral=True marks the message as stream-only (not persisted)
        msg = AIMessage(content="Hi")
        event = MessageCustomEvent(message=msg, ephemeral=True)
        assert event.ephemeral is True

    def test_missing_message_raises(self):
        # message is a required field
        with pytest.raises(ValidationError):
            MessageCustomEvent()


class TestReactionCustomEvent:
    def test_valid_construction(self):
        # ReactionActionPayload is accepted and stored as-is
        payload = ReactionActionPayload(
            context_id="C123",
            message_id="msg1",
            reaction_key="thumbsup",
            operation="add",
        )
        event = ReactionCustomEvent(payload=payload)
        assert event.payload is payload

    def test_missing_payload_raises(self):
        # payload is a required field
        with pytest.raises(ValidationError):
            ReactionCustomEvent()


class TestTaskUpdateCustomEvent:
    def test_with_message_only(self):
        # message-only update leaves metadata as None
        msg = AIMessage(content="Done")
        event = TaskUpdateCustomEvent(message=msg)
        assert event.message is msg
        assert event.metadata is None

    def test_with_metadata_only(self):
        # metadata-only update leaves message as None
        event = TaskUpdateCustomEvent(metadata={"progress": 100})
        assert event.metadata == {"progress": 100}
        assert event.message is None

    def test_with_both(self):
        # message and metadata can coexist in a single event
        msg = AIMessage(content="Done")
        event = TaskUpdateCustomEvent(message=msg, metadata={"step": "final"})
        assert event.message is msg
        assert event.metadata == {"step": "final"}

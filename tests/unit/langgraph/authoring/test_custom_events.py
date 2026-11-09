import pytest
from pydantic import ValidationError
from langchain_core.messages import AIMessage, AIMessageChunk
from a2a.types import Artifact

from aion.langgraph.authoring.events.custom_events import (
    ArtifactCustomEvent,
    MessageCustomEvent,
    ReactionCustomEvent,
    ThinkingCustomEvent,
)
from aion.core.a2a.extensions.messaging import MessageActionPayload, ReactionActionPayload


class TestArtifactCustomEvent:
    def test_valid_construction_with_defaults(self):
        # append=False, is_last_chunk=True, routing=None are the safe defaults
        artifact = Artifact(artifact_id="a1", name="test", parts=[])
        event = ArtifactCustomEvent(artifact=artifact)
        assert event.append is False
        assert event.is_last_chunk is True
        assert event.routing is None

    def test_streaming_flags_can_be_overridden(self):
        # append and is_last_chunk can be set for chunked streaming
        artifact = Artifact(artifact_id="a1", name="test", parts=[])
        event = ArtifactCustomEvent(artifact=artifact, append=True, is_last_chunk=False)
        assert event.append is True
        assert event.is_last_chunk is False

    def test_routing_can_be_set(self):
        # routing target is accepted and preserved as-is
        artifact = Artifact(artifact_id="a1", name="test", parts=[])
        routing = MessageActionPayload(trajectory="direct-message", context_id="D123")
        event = ArtifactCustomEvent(artifact=artifact, routing=routing)
        assert event.routing == routing

    def test_missing_artifact_raises(self):
        # artifact is a required field
        with pytest.raises(ValidationError):
            ArtifactCustomEvent()


class TestMessageCustomEvent:
    def test_valid_with_aimessage(self):
        # AIMessage is accepted; routing and metadata default to None
        msg = AIMessage(content="Hello")
        event = MessageCustomEvent(message=msg)
        assert event.message is msg
        assert event.routing is None
        assert event.metadata is None

    def test_valid_with_chunk(self):
        # AIMessageChunk is accepted (union type — both are valid)
        chunk = AIMessageChunk(content="chunk")
        event = MessageCustomEvent(message=chunk)
        assert event.message is chunk

    def test_missing_message_raises(self):
        # message is a required field
        with pytest.raises(ValidationError):
            MessageCustomEvent()


class TestThinkingCustomEvent:
    def test_valid_construction(self):
        # the thought's text is required; metadata defaults to None
        event = ThinkingCustomEvent(text="Looking up the account")
        assert event.text == "Looking up the account"
        assert event.metadata is None
        assert event.event_type == "thinking"

    def test_metadata_is_kept(self):
        event = ThinkingCustomEvent(text="Looking up the account", metadata={"step": "lookup"})
        assert event.metadata == {"step": "lookup"}

    def test_missing_text_raises(self):
        with pytest.raises(ValidationError):
            ThinkingCustomEvent()


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

import pytest
from unittest.mock import MagicMock
from a2a.types import Artifact
from langchain_core.messages import AIMessage, AIMessageChunk

from aion.langgraph.authoring.invocation.emitters import (
    emit_artifact,
    emit_card,
    emit_message,
    emit_reaction,
)
from aion.langgraph.authoring.events.custom_events import (
    ArtifactCustomEvent,
    CardCustomEvent,
    MessageCustomEvent,
    ReactionCustomEvent,
)
from aion.core.agent.invocation.card import Card
from aion.core.a2a.artifacts import file_artifact, url_artifact, data_artifact
from aion.core.a2a.extensions.messaging import MessageActionPayload, ReactionActionPayload


@pytest.fixture
def writer():
    return MagicMock()


class TestEmitArtifact:
    def test_emits_artifact_custom_event(self, writer):
        # pre-built artifact is wrapped in ArtifactCustomEvent and passed to writer
        artifact = url_artifact("https://example.com/r.pdf", mime_type="application/pdf")
        emit_artifact(writer, artifact)
        writer.assert_called_once()
        event = writer.call_args[0][0]
        assert isinstance(event, ArtifactCustomEvent)
        assert event.artifact is artifact

    def test_defaults_no_routing_no_append(self, writer):
        # routing is None by default, append=False, is_last_chunk=True
        artifact = data_artifact({"x": 1})
        emit_artifact(writer, artifact)
        event = writer.call_args[0][0]
        assert event.routing is None
        assert event.append is False
        assert event.is_last_chunk is True

    def test_routing_propagates(self, writer):
        # explicit routing target is attached to the event
        routing = MessageActionPayload(trajectory="direct-message", context_id="D1")
        artifact = data_artifact({"x": 1})
        emit_artifact(writer, artifact, routing=routing)
        assert writer.call_args[0][0].routing == routing

    def test_streaming_flags_propagate(self, writer):
        # append and is_last_chunk are forwarded to the event unchanged
        artifact = data_artifact({})
        emit_artifact(writer, artifact, append=True, is_last_chunk=False)
        event = writer.call_args[0][0]
        assert event.append is True
        assert event.is_last_chunk is False


class TestEmitCard:
    def test_emits_card_custom_event(self, writer):
        # card is wrapped in CardCustomEvent and passed to writer
        card = Card(jsx="<Card><Text>Hi</Text></Card>")
        emit_card(writer, card)
        writer.assert_called_once()
        event = writer.call_args[0][0]
        assert isinstance(event, CardCustomEvent)
        assert event.card is card

    def test_routing_none_by_default(self, writer):
        # routing defaults to None when not specified
        card = Card(jsx="<Card/>")
        emit_card(writer, card)
        assert writer.call_args[0][0].routing is None

    def test_routing_propagates(self, writer):
        # explicit routing target is attached to the event
        routing = MessageActionPayload(trajectory="conversation", context_id="C123")
        card = Card(jsx="<Card/>")
        emit_card(writer, card, routing=routing)
        assert writer.call_args[0][0].routing == routing


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
        from aion.core.a2a.extensions.messaging import MessageActionPayload
        routing = MessageActionPayload(trajectory="conversation", context_id="C1")
        emit_message(writer, AIMessage(content="Hi"), routing=routing)
        assert writer.call_args[0][0].routing == routing

    def test_routing_none_by_default(self, writer):
        # routing is None when not specified (uses distribution default)
        emit_message(writer, AIMessage(content="Hi"))
        assert writer.call_args[0][0].routing is None


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

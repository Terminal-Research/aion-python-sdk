import pytest
from unittest.mock import MagicMock
from a2a.types import Artifact
from langchain_core.messages import AIMessage, AIMessageChunk

from aion.langgraph.authoring.invocation.emitters import (
    aemit_message,
    emit_artifact,
    emit_card,
    emit_message,
    emit_reaction,
    emit_thinking,
)
from aion.langgraph.authoring.events.custom_events import (
    ArtifactCustomEvent,
    CardCustomEvent,
    MessageCustomEvent,
    ReactionCustomEvent,
    ThinkingCustomEvent,
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

    def test_a_message_without_an_id_gets_one_on_itself(self, writer):
        # the same object, returned into the graph's state, carries the id it was sent under
        msg = AIMessage(content="Hello")
        emit_message(writer, msg)
        assert msg.id
        assert writer.call_args[0][0].message.id == msg.id

    def test_a_message_keeps_its_own_id(self, writer):
        msg = AIMessage(content="Hello", id="m-1")
        emit_message(writer, msg)
        assert msg.id == "m-1"

    def test_a_chunk_is_left_without_an_id(self, writer):
        # a chunk's id groups the chunks of one model call; the SDK does not invent one
        chunk = AIMessageChunk(content="Hello")
        emit_message(writer, chunk)
        assert chunk.id is None

    def test_emits_message_event_with_chunk(self, writer):
        # AIMessageChunk is accepted and wrapped in MessageCustomEvent
        chunk = AIMessageChunk(content="Hello")
        emit_message(writer, chunk)
        event = writer.call_args[0][0]
        assert isinstance(event, MessageCustomEvent)
        assert event.message is chunk

    def test_metadata_propagates(self, writer):
        # user metadata is forwarded to the event unchanged
        emit_message(writer, AIMessage(content="Hi"), metadata={"trace_id": "abc"})
        assert writer.call_args[0][0].metadata == {"trace_id": "abc"}

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


class TestEmitMessageStream:
    """An iterator streams as one section, ended by the whole message, which is returned."""

    @staticmethod
    def _sent(writer) -> list:
        return [call.args[0].message for call in writer.call_args_list]

    def test_a_message_is_returned(self, writer):
        msg = AIMessage(content="Hello")
        assert emit_message(writer, msg) is msg

    def test_an_iterator_streams_its_items_then_the_whole_message(self, writer):
        result = emit_message(writer, iter(["It ", AIMessageChunk(content="is sunny.")]))

        sent = self._sent(writer)
        assert [type(m) for m in sent] == [AIMessageChunk, AIMessageChunk, AIMessage]
        assert [m.text for m in sent] == ["It ", "is sunny.", "It is sunny."]
        assert result is sent[-1]
        assert result.id

    def test_every_item_of_an_iterator_carries_routing_and_metadata(self, writer):
        routing = MessageActionPayload(trajectory="conversation", context_id="C1")
        emit_message(writer, iter(["a", "b"]), routing=routing, metadata={"k": "v"})

        events = [call.args[0] for call in writer.call_args_list]
        assert all(event.routing == routing and event.metadata == {"k": "v"} for event in events)

    def test_an_iterator_without_text_sends_no_whole_message(self, writer):
        assert emit_message(writer, iter([])) is None
        writer.assert_not_called()

    def test_an_iterator_that_raises_ends_with_what_it_yielded(self, writer):
        def answer():
            yield "partial"
            raise RuntimeError("stream broke")

        result = emit_message(writer, answer())

        assert result.text == "partial"
        assert isinstance(self._sent(writer)[-1], AIMessage)

    def test_an_async_iterator_is_for_aemit_message(self, writer):
        async def answer():
            yield "It is sunny."

        with pytest.raises(TypeError, match="aemit_message"):
            emit_message(writer, answer())
        writer.assert_not_called()

    def test_text_is_not_an_iterator_of_chunks(self, writer):
        with pytest.raises(TypeError):
            emit_message(writer, "It is sunny.")
        writer.assert_not_called()

    async def test_aemit_message_streams_an_async_iterator(self, writer):
        async def answer():
            yield "It "
            yield "is sunny."

        result = await aemit_message(writer, answer())

        assert [m.text for m in self._sent(writer)] == ["It ", "is sunny.", "It is sunny."]
        assert result is self._sent(writer)[-1]

    async def test_aemit_message_emits_anything_else_as_emit_message_does(self, writer):
        msg = AIMessage(content="Done")
        assert await aemit_message(writer, msg) is msg
        assert await aemit_message(writer, iter(["a"])) == self._sent(writer)[-1]

    def test_aemit_message_is_exported_from_the_authoring_package(self):
        from aion.langgraph.authoring import aemit_message as exported

        assert exported is aemit_message


class TestEmitThinking:
    def test_exported_from_the_authoring_package(self):
        # the README imports it from the package itself
        from aion.langgraph.authoring import emit_thinking as exported
        assert exported is emit_thinking

    def test_emits_thinking_custom_event(self, writer):
        # the thought is wrapped in ThinkingCustomEvent and passed to writer
        emit_thinking(writer, "Looking up the account")
        writer.assert_called_once()
        event = writer.call_args[0][0]
        assert isinstance(event, ThinkingCustomEvent)
        assert event.text == "Looking up the account"
        assert event.metadata is None

    def test_metadata_propagates(self, writer):
        emit_thinking(writer, "Looking up the account", metadata={"step": "lookup"})
        assert writer.call_args[0][0].metadata == {"step": "lookup"}


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

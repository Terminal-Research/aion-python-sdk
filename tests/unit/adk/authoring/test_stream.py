"""Tests for ADK emit_* streaming helpers and the Thread methods built on them."""

import asyncio
from aion.adk.authoring.constants import AION_OUTPUT_KEY, AION_ROUTING_KEY
from aion.adk.authoring.invocation.emitters import emit_artifact, emit_card, emit_reaction, emit_message, emit_thinking
from aion.adk.authoring.invocation.event_metadata import (
    get_aion_output,
    get_aion_user_metadata,
    is_thinking_event,
    stays_out_of_session,
)
from aion.adk.authoring.invocation.invocation_context import AionInvocationContext
from aion.core.a2a import FileActionPayload, data_artifact, file_artifact, url_artifact
from aion.core.a2a.extensions.messaging import MessageActionPayload, ReactionActionPayload
from aion.core.agent.invocation.card import Card
from a2a.types import Artifact, Part
from google.adk.events import Event
from google.protobuf.json_format import ParseDict
from unittest.mock import AsyncMock, MagicMock, patch

THREAD_EMITTER_PATH = "aion.adk.authoring.invocation.thread.get_adk_emitter"


def make_routing() -> MessageActionPayload:
    return MessageActionPayload(
        trajectory="direct-message",
        context_id="D06DM456",
        reply_to_message_id="1728162300.551219",
    )


class TestEmitText:
    def test_emits_event_with_text_content(self):
        emitter = MagicMock()
        emit_message(emitter, "hello")

        emitter.assert_called_once()
        event: Event = emitter.call_args[0][0]
        assert isinstance(event, Event)
        assert event.partial is False
        assert event.content.parts[0].text == "hello"

    def test_no_custom_metadata_without_routing_or_metadata(self):
        emitter = MagicMock()
        emit_message(emitter, "hello")

        event: Event = emitter.call_args[0][0]
        assert event.custom_metadata is None

    def test_routing_embedded_in_custom_metadata(self):
        emitter = MagicMock()
        emit_message(emitter, "hello", routing=make_routing())

        event: Event = emitter.call_args[0][0]
        assert event.custom_metadata is not None
        assert AION_ROUTING_KEY in event.custom_metadata
        assert event.custom_metadata[AION_ROUTING_KEY]["contextId"] == "D06DM456"

    def test_user_metadata_kept_in_custom_metadata(self):
        emitter = MagicMock()
        emit_message(emitter, "hello", metadata={"trace_id": "abc"})

        event: Event = emitter.call_args[0][0]
        assert event.custom_metadata == {"trace_id": "abc"}
        assert get_aion_output(event) is None


class TestEmitThinking:
    def test_emits_complete_event_marked_as_thought(self):
        emitter = MagicMock()
        emit_thinking(emitter, "Looking up the account...")

        emitter.assert_called_once()
        event: Event = emitter.call_args[0][0]
        assert event.partial is False
        assert event.content.parts[0].text == "Looking up the account..."
        output = get_aion_output(event)
        assert output is not None
        assert output.thinking is True
        assert is_thinking_event(event)

    def test_thought_names_no_artifact_card_or_reaction(self):
        emitter = MagicMock()
        emit_thinking(emitter, "Looking up the account...")

        output = get_aion_output(emitter.call_args[0][0])
        assert output.artifact is None
        assert output.card is None
        assert output.reaction is None

    def test_user_metadata_kept_beside_the_thought_marker(self):
        emitter = MagicMock()
        emit_thinking(emitter, "Looking up the account...", metadata={"step": "lookup"})

        event: Event = emitter.call_args[0][0]
        assert event.custom_metadata["step"] == "lookup"
        assert AION_OUTPUT_KEY in event.custom_metadata
        assert get_aion_user_metadata(event) == {"step": "lookup"}

    def test_caller_metadata_dict_is_not_modified(self):
        emitter = MagicMock()
        metadata = {"step": "lookup"}
        emit_thinking(emitter, "Looking up the account...", metadata=metadata)

        assert metadata == {"step": "lookup"}

    def test_plain_message_is_not_a_thought(self):
        emitter = MagicMock()
        emit_message(emitter, "hello")

        assert not is_thinking_event(emitter.call_args[0][0])


class TestThreadThinking:
    @staticmethod
    def make_thread():
        from aion.adk.authoring.invocation.thread import Thread
        return Thread(
            context=MagicMock(),
            context_id="C123",
            parent_context_id=None,
            network="Slack",
            default_reply_target="C123",
        )

    def test_thinking_emits_a_thought(self):
        emitter = MagicMock()
        thread = self.make_thread()

        with patch(THREAD_EMITTER_PATH, return_value=emitter):
            asyncio.run(thread.thinking("Looking up the account...", metadata={"step": "lookup"}))

        emitter.assert_called_once()
        event: Event = emitter.call_args[0][0]
        assert is_thinking_event(event)
        assert event.content.parts[0].text == "Looking up the account..."
        assert get_aion_user_metadata(event) == {"step": "lookup"}

    def test_thinking_rejects_non_string_content(self):
        emitter = MagicMock()
        thread = self.make_thread()

        with patch(THREAD_EMITTER_PATH, return_value=emitter):
            asyncio.run(thread.thinking({"not": "supported"}))

        emitter.assert_not_called()

    def test_thinking_without_emitter_does_nothing(self):
        thread = self.make_thread()

        with patch(THREAD_EMITTER_PATH, return_value=None):
            assert asyncio.run(thread.thinking("Looking up the account...")) is None


class TestEmitCard:
    def test_jsx_card_has_aion_output_card(self):
        emitter = MagicMock()
        card = Card(jsx="<Card><Text>Hello</Text></Card>")
        emit_card(emitter, card)

        event: Event = emitter.call_args[0][0]
        output = get_aion_output(event)
        assert output.card is not None
        assert output.card.url is None

    def test_jsx_card_content_contains_jsx(self):
        emitter = MagicMock()
        card = Card(jsx="<Card><Text>Hi</Text></Card>")
        emit_card(emitter, card)

        event: Event = emitter.call_args[0][0]
        assert event.content.parts[0].text == card.jsx

    def test_url_card_has_url_in_output(self):
        emitter = MagicMock()
        card = Card(url="https://my-site.com/card")
        emit_card(emitter, card)

        event: Event = emitter.call_args[0][0]
        output = get_aion_output(event)
        assert output.card.url == "https://my-site.com/card"

    def test_url_card_has_empty_content_parts(self):
        emitter = MagicMock()
        card = Card(url="https://my-site.com/card")
        emit_card(emitter, card)

        event: Event = emitter.call_args[0][0]
        assert event.content.parts == []

    def test_routing_embedded_in_custom_metadata(self):
        emitter = MagicMock()
        card = Card(jsx="<Card/>")
        emit_card(emitter, card, routing=make_routing())

        event: Event = emitter.call_args[0][0]
        assert AION_ROUTING_KEY in event.custom_metadata
        assert event.custom_metadata[AION_ROUTING_KEY]["contextId"] == "D06DM456"


def make_mock_ctx() -> MagicMock:
    """Build a mock that passes isinstance(ctx, AionInvocationContext) with a working artifact_service."""
    ctx = MagicMock()
    ctx.__class__ = AionInvocationContext
    ctx.app_name = "test_app"
    ctx.user_id = "user_1"
    ctx.session.id = "session_1"
    ctx.artifact_service.save_artifact = AsyncMock(return_value=0)
    return ctx


class TestEmitArtifact:
    """The artifact is published as it is, as emit_artifact does in LangGraph; ADK's artifact service is not used."""

    def _published(self, artifact, **kwargs):
        emitter = MagicMock()
        ctx = make_mock_ctx()
        asyncio.run(emit_artifact(emitter, ctx, artifact, **kwargs))
        event: Event = emitter.call_args[0][0]
        return event, ctx

    def test_the_whole_artifact_travels_in_the_event(self):
        artifact = data_artifact({"a": 1}, name="result")
        artifact.parts.append(Part(text="second part"))
        artifact.metadata.update({"owner": "agent-x"})

        event, ctx = self._published(artifact)

        published = get_aion_output(event).published
        assert ParseDict(published.artifact, Artifact()) == artifact
        assert (published.append, published.last_chunk) == (False, True)
        ctx.artifact_service.save_artifact.assert_not_called()

    def test_chunks_say_whether_they_extend_and_end_the_artifact(self):
        event, _ = self._published(url_artifact("https://example.com/r.pdf", mime_type="application/pdf", name="r"), append=True, is_last_chunk=False)

        published = get_aion_output(event).published
        assert (published.append, published.last_chunk) == (True, False)

    def test_the_agents_metadata_and_routing_ride_beside_it(self):
        routing = make_routing()

        event, _ = self._published(file_artifact(b"hello", mime_type="text/plain", name="f.txt"), routing=routing, metadata={"owner": "x"})

        assert get_aion_user_metadata(event) == {"owner": "x"}
        assert AION_ROUTING_KEY in event.custom_metadata

    def test_the_event_stays_out_of_the_session(self):
        event, _ = self._published(url_artifact("https://example.com/r.pdf", mime_type="application/pdf", name="r"))

        assert stays_out_of_session(event)
        assert event.content is None


class TestEmitReaction:
    def test_emits_reaction_event(self):
        emitter = MagicMock()
        payload = ReactionActionPayload(
            context_id="C123",
            message_id="msg-1",
            reaction_key="thumbsup",
            operation="add",
        )
        emit_reaction(emitter, payload)

        emitter.assert_called_once()
        event: Event = emitter.call_args[0][0]
        output = get_aion_output(event)
        assert output.reaction is not None
        assert output.reaction.reaction_key == "thumbsup"
        assert output.reaction.context_id == "C123"
        assert output.reaction.operation == "add"

    def test_remove_operation(self):
        emitter = MagicMock()
        payload = ReactionActionPayload(
            context_id="C123",
            message_id="msg-1",
            reaction_key="thumbsup",
            operation="remove",
        )
        emit_reaction(emitter, payload)

        event: Event = emitter.call_args[0][0]
        output = get_aion_output(event)
        assert output.reaction.operation == "remove"

    def test_display_value_passed_through(self):
        emitter = MagicMock()
        payload = ReactionActionPayload(
            context_id="C123",
            message_id="msg-1",
            reaction_key="thumbsup",
            operation="add",
            display_value=":thumbsup:",
        )
        emit_reaction(emitter, payload)

        event: Event = emitter.call_args[0][0]
        output = get_aion_output(event)
        assert output.reaction.display_value == ":thumbsup:"

"""Tests for ADK emit_* streaming helpers."""

import asyncio
from aion.adk.authoring.constants import AION_OUTPUT_KEY, AION_ROUTING_KEY
from aion.adk.authoring.invocation.emitters import emit_artifact, emit_card, emit_reaction, emit_message
from aion.adk.authoring.invocation.event_metadata import get_aion_output
from aion.adk.authoring.invocation.invocation_context import AionInvocationContext
from aion.core.a2a import data_artifact, file_artifact, url_artifact
from aion.core.a2a.enums import ArtifactId
from aion.core.a2a.extensions.messaging import MessageActionPayload, ReactionActionPayload
from aion.core.agent.invocation.card import Card
from google.adk.events import Event
from unittest.mock import AsyncMock, MagicMock


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

    def test_no_custom_metadata_without_routing_or_ephemeral(self):
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

    def test_ephemeral_sets_aion_output_artifact(self):
        emitter = MagicMock()
        emit_message(emitter, "typing...", ephemeral=True)

        event: Event = emitter.call_args[0][0]
        output = get_aion_output(event)
        assert output is not None
        assert output.artifact is not None
        assert output.artifact.artifact_id == ArtifactId.EPHEMERAL_MESSAGE.value

    def test_ephemeral_text_in_content_not_duplicated(self):
        """Text is in event.content only — aion:output carries just the routing hint."""
        emitter = MagicMock()
        emit_message(emitter, "typing...", ephemeral=True)

        event: Event = emitter.call_args[0][0]
        assert event.content is not None
        assert event.content.parts[0].text == "typing..."
        # aion:output.artifact must NOT contain parts/data — only routing metadata
        output = get_aion_output(event)
        assert "parts" not in output.model_dump(exclude_none=True).get("artifact", {})

    def test_ephemeral_with_routing_has_both_keys(self):
        emitter = MagicMock()
        emit_message(emitter, "typing...", routing=make_routing(), ephemeral=True)

        event: Event = emitter.call_args[0][0]
        assert AION_OUTPUT_KEY in event.custom_metadata
        assert AION_ROUTING_KEY in event.custom_metadata


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
    def test_url_artifact_routing_hint_in_metadata(self):
        emitter = MagicMock()
        ctx = make_mock_ctx()
        artifact = url_artifact("https://example.com/r.pdf", mime_type="application/pdf", name="report")
        asyncio.run(emit_artifact(emitter, ctx, artifact))

        event: Event = emitter.call_args[0][0]
        output = get_aion_output(event)
        assert output.artifact is not None
        assert output.artifact.artifact_id == artifact.artifact_id
        assert output.artifact.artifact_name == "report"

    def test_url_artifact_saved_to_artifact_service(self):
        emitter = MagicMock()
        ctx = make_mock_ctx()
        artifact = url_artifact("https://example.com/r.pdf", mime_type="application/pdf", name="report")
        asyncio.run(emit_artifact(emitter, ctx, artifact))

        ctx.artifact_service.save_artifact.assert_called_once()
        call_kwargs = ctx.artifact_service.save_artifact.call_args.kwargs
        assert call_kwargs["filename"] == "report"
        assert call_kwargs["artifact"].file_data.file_uri == "https://example.com/r.pdf"

    def test_bytes_artifact_saved_to_artifact_service(self):
        emitter = MagicMock()
        ctx = make_mock_ctx()
        artifact = file_artifact(b"hello", mime_type="text/plain", name="file.txt")
        asyncio.run(emit_artifact(emitter, ctx, artifact))

        ctx.artifact_service.save_artifact.assert_called_once()
        call_kwargs = ctx.artifact_service.save_artifact.call_args.kwargs
        assert call_kwargs["filename"] == "file.txt"
        assert call_kwargs["artifact"].inline_data.data == b"hello"

    def test_data_artifact_saved_to_artifact_service(self):
        emitter = MagicMock()
        ctx = make_mock_ctx()
        artifact = data_artifact({"score": 42}, name="result")
        asyncio.run(emit_artifact(emitter, ctx, artifact))

        ctx.artifact_service.save_artifact.assert_called_once()
        call_kwargs = ctx.artifact_service.save_artifact.call_args.kwargs
        assert call_kwargs["filename"] == "result"

    def test_emits_event_with_artifact_delta(self):
        emitter = MagicMock()
        ctx = make_mock_ctx()
        ctx.artifact_service.save_artifact = AsyncMock(return_value=3)
        artifact = url_artifact("https://example.com/r.pdf", mime_type="application/pdf", name="report")
        asyncio.run(emit_artifact(emitter, ctx, artifact))

        event: Event = emitter.call_args[0][0]
        assert event.actions.artifact_delta == {"report": 3}
        assert event.content is None

    def test_routing_embedded_in_metadata(self):
        emitter = MagicMock()
        ctx = make_mock_ctx()
        artifact = url_artifact("https://example.com/f.pdf", mime_type="application/pdf")
        asyncio.run(emit_artifact(emitter, ctx, artifact, routing=make_routing()))

        event: Event = emitter.call_args[0][0]
        assert AION_ROUTING_KEY in event.custom_metadata
        assert AION_OUTPUT_KEY in event.custom_metadata

    def test_no_artifact_service_logs_warning_and_skips(self):
        emitter = MagicMock()
        ctx = MagicMock()
        ctx.artifact_service = None
        artifact = url_artifact("https://example.com/r.pdf", mime_type="application/pdf")
        asyncio.run(emit_artifact(emitter, ctx, artifact))

        emitter.assert_not_called()

    def test_metadata_contains_only_routing_hint(self):
        """aion:output.artifact carries only artifact_id/name — no data."""
        emitter = MagicMock()
        ctx = make_mock_ctx()
        artifact = url_artifact("https://example.com/r.pdf", mime_type="application/pdf", name="doc")
        asyncio.run(emit_artifact(emitter, ctx, artifact))

        event: Event = emitter.call_args[0][0]
        output = get_aion_output(event)
        artifact_hint = output.model_dump(exclude_none=True)["artifact"]
        assert set(artifact_hint.keys()) <= {"artifact_id", "artifact_name"}


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

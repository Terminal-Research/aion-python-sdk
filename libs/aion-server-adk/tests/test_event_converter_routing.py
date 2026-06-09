"""Tests for ADKToA2AEventConverter routing via aion:routing in custom_metadata."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from google.adk.events import Event, EventActions
from google.genai import types
from a2a.types import TaskArtifactUpdateEvent, TaskStatusUpdateEvent

from aion.adk.authoring.constants import AION_OUTPUT_KEY, AION_ROUTING_KEY
from aion.adk.authoring.invocation.output import AionOutput, ArtifactOutput, CardOutput
from aion.core.a2a import data_artifact, file_artifact
from aion.core.constants import MESSAGING_EXTENSION_URI_V1, CARDS_EXTENSION_URI_V1
from aion.core.agent.invocation.card import Card
from aion.core.a2a.extensions.messaging import MessageActionPayload

from aion.adk.server.execution.event_converter import ADKToA2AEventConverter


def make_converter() -> ADKToA2AEventConverter:
    return ADKToA2AEventConverter(task_id="task-1", context_id="ctx-1")


def make_routing() -> MessageActionPayload:
    return MessageActionPayload(
        trajectory="direct-message",
        context_id="D06DM456",
        reply_to_message_id="1728162300.551219",
    )


def make_text_event(text: str, routing: MessageActionPayload | None = None) -> Event:
    meta = {}
    if routing is not None:
        meta[AION_ROUTING_KEY] = routing.model_dump(by_alias=True, exclude_none=True)
    return Event(
        author="agent",
        content=types.Content(parts=[types.Part(text=text)], role="model"),
        partial=False,
        custom_metadata=meta or None,
    )


def make_card_event(card: Card, routing: MessageActionPayload | None = None) -> Event:
    meta = {AION_OUTPUT_KEY: AionOutput(card=CardOutput()).model_dump(exclude_none=True)}
    if routing is not None:
        meta[AION_ROUTING_KEY] = routing.model_dump(by_alias=True, exclude_none=True)
    return Event(
        author="agent",
        content=types.Content(parts=[types.Part(text=card.jsx)], role="model"),
        partial=False,
        custom_metadata=meta,
    )


class TestExtractRouting:
    def test_returns_none_when_no_metadata(self):
        converter = make_converter()
        assert converter._extract_routing(None) is None

    def test_returns_none_when_key_absent(self):
        converter = make_converter()
        assert converter._extract_routing({"aion:output": {}}) is None

    def test_returns_payload_when_key_present(self):
        converter = make_converter()
        routing = make_routing()
        meta = {AION_ROUTING_KEY: routing.model_dump(by_alias=True, exclude_none=True)}
        result = converter._extract_routing(meta)
        assert isinstance(result, MessageActionPayload)
        assert result.context_id == "D06DM456"
        assert result.reply_to_message_id == "1728162300.551219"


class TestTextMessageRouting:
    async def test_text_event_with_routing_adds_extension_part_and_extension(self):
        converter = make_converter()
        routing = make_routing()
        event = make_text_event("hello", routing=routing)

        results = await converter.convert(event)

        assert len(results) == 1
        assert isinstance(results[0], TaskStatusUpdateEvent)
        msg = results[0].status.message
        assert len(msg.parts) == 2
        assert MESSAGING_EXTENSION_URI_V1 in msg.extensions

    async def test_text_event_without_routing_has_no_extensions(self):
        converter = make_converter()
        event = make_text_event("hello")

        results = await converter.convert(event)

        assert len(results) == 1
        msg = results[0].status.message
        assert len(msg.parts) == 1
        assert not msg.extensions


class TestCardRouting:
    async def test_card_event_with_routing_adds_extension_part_and_both_extensions(self):
        converter = make_converter()
        routing = make_routing()
        card = Card(jsx="<Card><Text>hi</Text></Card>")
        event = make_card_event(card, routing=routing)

        results = await converter.convert(event)

        assert len(results) == 1
        msg = results[0].status.message
        assert len(msg.parts) == 2
        assert CARDS_EXTENSION_URI_V1 in msg.extensions
        assert MESSAGING_EXTENSION_URI_V1 in msg.extensions

    async def test_card_event_without_routing_has_only_cards_extension(self):
        converter = make_converter()
        card = Card(jsx="<Card><Text>hi</Text></Card>")
        event = make_card_event(card)

        results = await converter.convert(event)

        assert len(results) == 1
        msg = results[0].status.message
        assert len(msg.parts) == 1
        assert CARDS_EXTENSION_URI_V1 in msg.extensions
        assert MESSAGING_EXTENSION_URI_V1 not in msg.extensions


def make_artifact_service(part_to_return) -> MagicMock:
    """Mock artifact_service that returns a given part on load_artifact."""
    svc = MagicMock()
    svc.save_artifact = AsyncMock(return_value=0)
    svc.load_artifact = AsyncMock(return_value=part_to_return)
    return svc


def make_converter_with_ctx(artifact_service=None) -> ADKToA2AEventConverter:
    ctx = MagicMock()
    ctx.app_name = "app"
    ctx.user_id = "user"
    ctx.session.id = "sess"
    ctx.artifact_service = artifact_service
    return ADKToA2AEventConverter(task_id="task-1", context_id="ctx-1", ctx=ctx)


def make_artifact_delta_event(
    artifact,
    loaded_part,
    routing: MessageActionPayload | None = None,
) -> tuple[Event, MagicMock]:
    """Build an ADK artifact_delta Event + mock ctx (as emit_artifact would produce)."""
    meta = {
        AION_OUTPUT_KEY: AionOutput(
            artifact=ArtifactOutput(
                artifact_id=artifact.artifact_id,
                artifact_name=artifact.name or None,
            )
        ).model_dump(exclude_none=True)
    }
    if routing is not None:
        from aion.adk.authoring.constants import AION_ROUTING_KEY
        meta[AION_ROUTING_KEY] = routing.model_dump(by_alias=True, exclude_none=True)

    filename = artifact.name or artifact.artifact_id
    event = Event(
        author="agent",
        content=None,
        partial=False,
        actions=EventActions(artifact_delta={filename: 0}),
        custom_metadata=meta,
    )
    svc = make_artifact_service(loaded_part)
    return event, svc


class TestArtifactOutput:
    async def test_url_file_artifact_produces_task_artifact_update_event(self):
        artifact = file_artifact(url="https://example.com/r.pdf", mime_type="application/pdf", name="report")
        loaded_part = types.Part(file_data=types.FileData(file_uri="https://example.com/r.pdf", mime_type="application/pdf"))
        event, svc = make_artifact_delta_event(artifact, loaded_part)
        converter = make_converter_with_ctx(artifact_service=svc)

        results = await converter.convert(event)

        assert len(results) == 1
        assert isinstance(results[0], TaskArtifactUpdateEvent)
        assert results[0].artifact.name == "report"
        assert results[0].artifact.artifact_id == artifact.artifact_id
        assert results[0].artifact.parts[0].url == "https://example.com/r.pdf"

    async def test_data_artifact_uses_hint_name(self):
        from aion.adk.authoring.transformers import convert_a2a_part_to_genai_part

        artifact = data_artifact({"score": 42}, name="result")
        # Use the actual transformer to produce the correct encoded genai part
        loaded_part = convert_a2a_part_to_genai_part(artifact.parts[0])
        event, svc = make_artifact_delta_event(artifact, loaded_part)
        converter = make_converter_with_ctx(artifact_service=svc)

        results = await converter.convert(event)

        assert len(results) == 1
        assert isinstance(results[0], TaskArtifactUpdateEvent)
        assert results[0].artifact.name == "result"
        assert results[0].artifact.artifact_id == artifact.artifact_id

    async def test_bytes_file_artifact_roundtrips(self):
        artifact = file_artifact(data=b"hello bytes", mime_type="text/plain", name="bytes_file")
        loaded_part = types.Part(inline_data=types.Blob(data=b"hello bytes", mime_type="text/plain"))
        event, svc = make_artifact_delta_event(artifact, loaded_part)
        converter = make_converter_with_ctx(artifact_service=svc)

        results = await converter.convert(event)

        assert len(results) == 1
        assert isinstance(results[0], TaskArtifactUpdateEvent)
        assert results[0].artifact.name == "bytes_file"
        assert results[0].artifact.parts[0].raw == b"hello bytes"

    async def test_artifact_event_is_last_chunk(self):
        artifact = file_artifact(url="https://example.com/f.pdf", mime_type="application/pdf", name="doc")
        loaded_part = types.Part(file_data=types.FileData(file_uri="https://example.com/f.pdf", mime_type="application/pdf"))
        event, svc = make_artifact_delta_event(artifact, loaded_part)
        converter = make_converter_with_ctx(artifact_service=svc)

        results = await converter.convert(event)

        assert results[0].last_chunk is True
        assert results[0].append is False

    async def test_artifact_event_closes_open_stream_delta(self):
        """If streaming was active, STREAM_DELTA is closed before emitting artifact."""
        artifact = file_artifact(url="https://example.com/f.pdf", mime_type="application/pdf", name="doc")
        loaded_part = types.Part(file_data=types.FileData(file_uri="https://example.com/f.pdf", mime_type="application/pdf"))
        event, svc = make_artifact_delta_event(artifact, loaded_part)
        converter = make_converter_with_ctx(artifact_service=svc)

        partial_event = Event(
            author="agent",
            content=types.Content(parts=[types.Part(text="chunk")], role="model"),
            partial=True,
        )
        await converter.convert(partial_event)
        assert converter._streaming_started is True

        results = await converter.convert(event)

        assert len(results) == 2
        assert results[0].last_chunk is True   # stream delta close
        assert isinstance(results[1], TaskArtifactUpdateEvent)
        assert results[1].artifact.name == "doc"

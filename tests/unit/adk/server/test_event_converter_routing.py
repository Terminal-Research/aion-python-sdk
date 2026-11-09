"""Tests for ADKToA2AEventConverter routing via aion:routing in custom_metadata."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from google.adk.events import Event, EventActions
from google.genai import types
from a2a.types import TaskArtifactUpdateEvent, TaskStatusUpdateEvent

from aion.adk.authoring.constants import AION_OUTPUT_KEY, AION_ROUTING_KEY
from aion.adk.authoring.invocation.event_metadata import AionOutput, ArtifactOutput, CardOutput
from aion.core.a2a import ArtifactId, data_artifact, file_artifact, url_artifact
from aion.core.constants import MESSAGING_EXTENSION_URI_V1, CARDS_EXTENSION_URI_V1
from aion.core.agent.invocation.card import Card
from aion.core.a2a.extensions.messaging import MessageActionPayload, ReactionActionPayload

from aion.adk.authoring.invocation.event_metadata import get_aion_routing
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
        event = Event(author="agent", custom_metadata=None)
        assert get_aion_routing(event) is None

    def test_returns_none_when_key_absent(self):
        event = Event(author="agent", custom_metadata={"aion:output": {}})
        assert get_aion_routing(event) is None

    def test_returns_payload_when_key_present(self):
        routing = make_routing()
        event = Event(
            author="agent",
            custom_metadata={AION_ROUTING_KEY: routing.model_dump(by_alias=True, exclude_none=True)},
        )
        result = get_aion_routing(event)
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
        artifact = url_artifact("https://example.com/r.pdf", mime_type="application/pdf", name="report")
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
        artifact = file_artifact(b"hello bytes", mime_type="text/plain", name="bytes_file")
        loaded_part = types.Part(inline_data=types.Blob(data=b"hello bytes", mime_type="text/plain"))
        event, svc = make_artifact_delta_event(artifact, loaded_part)
        converter = make_converter_with_ctx(artifact_service=svc)

        results = await converter.convert(event)

        assert len(results) == 1
        assert isinstance(results[0], TaskArtifactUpdateEvent)
        assert results[0].artifact.name == "bytes_file"
        assert results[0].artifact.parts[0].raw == b"hello bytes"

    async def test_artifact_event_is_last_chunk(self):
        artifact = url_artifact("https://example.com/f.pdf", mime_type="application/pdf", name="doc")
        loaded_part = types.Part(file_data=types.FileData(file_uri="https://example.com/f.pdf", mime_type="application/pdf"))
        event, svc = make_artifact_delta_event(artifact, loaded_part)
        converter = make_converter_with_ctx(artifact_service=svc)

        results = await converter.convert(event)

        assert results[0].last_chunk is True
        assert results[0].append is False

    async def test_artifact_event_ends_open_stream_delta(self):
        """An artifact event during streaming ends the open section with the end mark, then emits the artifact."""
        artifact = url_artifact("https://example.com/f.pdf", mime_type="application/pdf", name="doc")
        loaded_part = types.Part(file_data=types.FileData(file_uri="https://example.com/f.pdf", mime_type="application/pdf"))
        event, svc = make_artifact_delta_event(artifact, loaded_part)
        converter = make_converter_with_ctx(artifact_service=svc)

        await converter.convert(make_partial_event("chunk"))
        results = await converter.convert(event)

        assert len(results) == 2
        final, artifact_event = results
        assert final.artifact.artifact_id == ArtifactId.STREAM_DELTA.value
        assert final.artifact.parts[0].text == ""
        assert (final.append, final.last_chunk) == (True, True)
        assert isinstance(artifact_event, TaskArtifactUpdateEvent)
        assert artifact_event.artifact.name == "doc"

        next_section = await converter.convert(make_partial_event("next"))
        assert next_section[0].append is False


def make_partial_event(text: str) -> Event:
    return Event(
        author="agent",
        content=types.Content(parts=[types.Part(text=text)], role="model"),
        partial=True,
    )


def make_thought_event(text: str) -> Event:
    return Event(
        author="agent",
        content=types.Content(parts=[types.Part(text=text)], role="model"),
        partial=False,
        custom_metadata={AION_OUTPUT_KEY: AionOutput(thinking=True).model_dump(exclude_none=True)},
    )


def make_reaction_event() -> Event:
    reaction = ReactionActionPayload(context_id="C123", message_id="msg-1", reaction_key="eyes", operation="add")
    return Event(
        author="agent",
        partial=False,
        custom_metadata={AION_OUTPUT_KEY: AionOutput(reaction=reaction).model_dump(exclude_none=True)},
    )


async def test_thinking_output_hint_produces_a_thought_instead_of_a_message():
    """A thought goes to aion:thinking-delta as one text part, not into a status message."""
    results = await make_converter().convert(make_thought_event("Considering the next step"))

    assert len(results) == 1
    assert isinstance(results[0], TaskArtifactUpdateEvent)
    assert results[0].artifact.artifact_id == ArtifactId.THINKING_DELTA.value
    assert [part.text for part in results[0].artifact.parts] == ["Considering the next step"]


async def test_reaction_output_hint_produces_the_reaction_artifact():
    results = await make_converter().convert(make_reaction_event())

    assert len(results) == 1
    assert results[0].artifact.artifact_id == ArtifactId.REACTION.value
    assert (results[0].append, results[0].last_chunk) == (False, True)


async def test_a_reaction_does_not_end_the_open_section():
    converter = make_converter()
    await converter.convert(make_partial_event("It "))

    reaction = await converter.convert(make_reaction_event())
    after = await converter.convert(make_partial_event("is sunny."))

    assert [event.artifact.artifact_id for event in reaction] == [ArtifactId.REACTION.value]
    assert after[0].append is True


def make_routed_partial_event(text: str, routing: MessageActionPayload) -> Event:
    return Event(
        author="agent",
        content=types.Content(parts=[types.Part(text=text)], role="model"),
        partial=True,
        custom_metadata={AION_ROUTING_KEY: routing.model_dump(by_alias=True, exclude_none=True)},
    )


def _contents(event: TaskArtifactUpdateEvent) -> list:
    return [part.text for part in event.artifact.parts if not MessageActionPayload.is_part(part)]


def _routed(event: TaskArtifactUpdateEvent) -> bool:
    return any(MessageActionPayload.is_part(part) for part in event.artifact.parts)


class TestArtifactRouting:
    async def test_a_routed_artifact_carries_its_target_as_a_part(self):
        artifact = url_artifact("https://example.com/r.pdf", mime_type="application/pdf", name="report")
        loaded_part = types.Part(file_data=types.FileData(file_uri="https://example.com/r.pdf", mime_type="application/pdf"))
        event, svc = make_artifact_delta_event(artifact, loaded_part, routing=make_routing())

        [result] = await make_converter_with_ctx(artifact_service=svc).convert(event)

        assert result.artifact.parts[0].url == "https://example.com/r.pdf"
        assert MessageActionPayload.is_part(result.artifact.parts[-1])
        assert list(result.artifact.extensions) == [MESSAGING_EXTENSION_URI_V1]

    async def test_an_artifact_without_a_target_has_none(self):
        artifact = url_artifact("https://example.com/r.pdf", mime_type="application/pdf", name="report")
        loaded_part = types.Part(file_data=types.FileData(file_uri="https://example.com/r.pdf", mime_type="application/pdf"))
        event, svc = make_artifact_delta_event(artifact, loaded_part)

        [result] = await make_converter_with_ctx(artifact_service=svc).convert(event)

        assert not _routed(result)
        assert list(result.artifact.extensions) == []


class TestStreamRouting:
    async def test_a_routed_section_carries_its_target_on_its_first_chunk_only(self):
        converter = make_converter()
        routing = make_routing()

        [first] = await converter.convert(make_routed_partial_event("one ", routing))
        [second] = await converter.convert(make_routed_partial_event("two", routing))
        [end] = await converter.convert(make_text_event("one two", routing))

        assert (_contents(first), _routed(first)) == (["one "], True)
        assert list(first.artifact.extensions) == [MESSAGING_EXTENSION_URI_V1]
        assert (_contents(second), _routed(second)) == (["two"], False)
        assert (_contents(end), _routed(end)) == ([""], False)
        assert (end.append, end.last_chunk) == (True, True)

    async def test_a_routed_section_the_run_left_open_ends_with_the_end_mark(self):
        converter = make_converter()
        await converter.convert(make_routed_partial_event("one", make_routing()))

        [end] = converter.finalize_stream()

        assert (_contents(end), _routed(end)) == ([""], False)
        assert end.last_chunk is True

    async def test_the_models_own_stream_carries_no_target(self):
        converter = make_converter()

        [chunk] = await converter.convert(make_partial_event("It is sunny."))
        [final] = converter.finalize_stream()

        assert not _routed(chunk)
        assert not _routed(final)

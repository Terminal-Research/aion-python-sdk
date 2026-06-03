"""Tests for ADKToA2AEventConverter routing via aion:routing in custom_metadata."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from google.adk.events import Event
from google.genai import types
from a2a.types import TaskStatusUpdateEvent

from aion.adk.authoring.constants import AION_ROUTING_KEY
from aion.adk.authoring.output import AionOutput, CardOutput
from aion.core.constants import MESSAGING_EXTENSION_URI_V1, CARDS_EXTENSION_URI_V1
from aion.core.agent.invocation.card import Card
from aion.core.types.a2a.extensions.messaging import MessageActionPayload

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
    meta = AionOutput(card=CardOutput()).to_custom_metadata()
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

"""Tests for Thread.post() routing via aion:routing in custom_metadata."""

import asyncio
from unittest.mock import MagicMock, patch

from aion.adk.authoring.constants import AION_ROUTING_KEY
from aion.core.a2a.extensions.messaging import MessageActionPayload

EMITTER_PATH = "aion.adk.authoring.invocation.thread.get_adk_emitter"


def make_routing() -> MessageActionPayload:
    return MessageActionPayload(
        trajectory="direct-message",
        context_id="D06DM456",
        reply_to_message_id="1728162300.551219",
    )


def make_thread():
    from unittest.mock import MagicMock
    from aion.adk.authoring.invocation.thread import Thread
    return Thread(
        context=MagicMock(),
        context_id="C123",
        parent_context_id=None,
        network="Slack",
        default_reply_target="C123",
    )


class TestPostWithRouting:
    def test_text_message_embeds_routing_in_custom_metadata(self):
        emitter = MagicMock()
        routing = make_routing()
        thread = make_thread()

        with patch(EMITTER_PATH, return_value=emitter):
            asyncio.run(thread.post("hello", target=routing))

        event = emitter.call_args[0][0]
        assert event.custom_metadata is not None
        assert AION_ROUTING_KEY in event.custom_metadata
        stored = event.custom_metadata[AION_ROUTING_KEY]
        assert stored["contextId"] == "D06DM456"
        assert stored["replyToMessageId"] == "1728162300.551219"

    def test_text_message_without_target_has_no_routing_key(self):
        emitter = MagicMock()
        thread = make_thread()

        with patch(EMITTER_PATH, return_value=emitter):
            asyncio.run(thread.post("hello"))

        event = emitter.call_args[0][0]
        assert event.custom_metadata is None or AION_ROUTING_KEY not in (event.custom_metadata or {})

    def test_card_embeds_routing_in_custom_metadata(self):
        from aion.core.agent.invocation.card import Card

        emitter = MagicMock()
        routing = make_routing()
        thread = make_thread()
        card = Card(jsx="<Card><Text>hi</Text></Card>")

        with patch(EMITTER_PATH, return_value=emitter):
            asyncio.run(thread.post(card, target=routing))

        event = emitter.call_args[0][0]
        assert AION_ROUTING_KEY in event.custom_metadata
        assert event.custom_metadata[AION_ROUTING_KEY]["contextId"] == "D06DM456"

    def test_async_iterator_final_event_embeds_routing(self):
        emitter = MagicMock()
        routing = make_routing()
        thread = make_thread()

        async def chunks():
            yield "chunk1"
            yield "chunk2"

        with patch(EMITTER_PATH, return_value=emitter):
            asyncio.run(thread.post(chunks(), target=routing))

        # last call is the final durable event
        final_event = emitter.call_args_list[-1][0][0]
        assert AION_ROUTING_KEY in final_event.custom_metadata
        assert final_event.custom_metadata[AION_ROUTING_KEY]["contextId"] == "D06DM456"

    def test_async_iterator_partial_chunks_have_no_routing(self):
        emitter = MagicMock()
        routing = make_routing()
        thread = make_thread()

        async def chunks():
            yield "a"
            yield "b"

        with patch(EMITTER_PATH, return_value=emitter):
            asyncio.run(thread.post(chunks(), target=routing))

        # partial events (all but last) must not carry routing
        for call in emitter.call_args_list[:-1]:
            partial_event = call[0][0]
            assert partial_event.partial is True
            assert AION_ROUTING_KEY not in (partial_event.custom_metadata or {})

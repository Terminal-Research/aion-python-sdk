"""Unit tests for event_metadata typed accessors."""

import pytest
from google.adk.events import Event
from google.genai import types

from aion.adk.authoring.constants import AION_OUTPUT_KEY, AION_ROUTING_KEY
from aion.adk.authoring.invocation.event_metadata import (
    AionOutput,
    ArtifactOutput,
    CardOutput,
    get_aion_output,
    get_aion_routing,
    get_aion_user_metadata,
)
from aion.core.a2a.extensions.messaging import MessageActionPayload


def make_event(custom_metadata=None) -> Event:
    return Event(
        author="agent",
        content=types.Content(parts=[types.Part(text="hi")], role="model"),
        custom_metadata=custom_metadata,
    )


def make_routing() -> MessageActionPayload:
    return MessageActionPayload(
        trajectory="direct-message",
        context_id="C123",
        reply_to_message_id="msg-1",
    )


class TestGetAionOutput:
    def test_returns_none_when_no_metadata(self):
        assert get_aion_output(make_event()) is None

    def test_returns_none_when_key_absent(self):
        event = make_event({AION_ROUTING_KEY: {"trajectory": "dm"}})
        assert get_aion_output(event) is None

    def test_returns_typed_output_for_artifact(self):
        hint = AionOutput(artifact=ArtifactOutput(artifact_id="art-1", artifact_name="doc"))
        event = make_event({AION_OUTPUT_KEY: hint.model_dump(exclude_none=True)})
        result = get_aion_output(event)
        assert isinstance(result, AionOutput)
        assert result.artifact.artifact_id == "art-1"
        assert result.artifact.artifact_name == "doc"

    def test_returns_typed_output_for_card(self):
        hint = AionOutput(card=CardOutput(url="https://example.com/card"))
        event = make_event({AION_OUTPUT_KEY: hint.model_dump(exclude_none=True)})
        result = get_aion_output(event)
        assert result.card is not None
        assert result.card.url == "https://example.com/card"


class TestGetAionRouting:
    def test_returns_none_when_no_metadata(self):
        assert get_aion_routing(make_event()) is None

    def test_returns_none_when_key_absent(self):
        event = make_event({AION_OUTPUT_KEY: {}})
        assert get_aion_routing(event) is None

    def test_returns_typed_payload(self):
        routing = make_routing()
        event = make_event({AION_ROUTING_KEY: routing.model_dump(by_alias=True, exclude_none=True)})
        result = get_aion_routing(event)
        assert isinstance(result, MessageActionPayload)
        assert result.context_id == "C123"
        assert result.reply_to_message_id == "msg-1"


class TestGetAionUserMetadata:
    def test_returns_none_when_no_metadata(self):
        assert get_aion_user_metadata(make_event()) is None

    def test_returns_none_when_only_service_keys(self):
        event = make_event({AION_OUTPUT_KEY: {}, AION_ROUTING_KEY: {}})
        assert get_aion_user_metadata(event) is None

    def test_returns_user_keys_only(self):
        event = make_event({
            "source": "slack",
            "user_id": "U123",
            AION_OUTPUT_KEY: {},
            AION_ROUTING_KEY: {},
        })
        result = get_aion_user_metadata(event)
        assert result == {"source": "slack", "user_id": "U123"}

    def test_returns_none_when_user_meta_empty_after_filter(self):
        event = make_event({AION_OUTPUT_KEY: {}})
        assert get_aion_user_metadata(event) is None

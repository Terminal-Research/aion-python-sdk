"""``reply_of`` reads the user's reply to a question by one rule for every framework."""

from __future__ import annotations

from typing import Any

import pytest
from a2a.types import Message, Part, Role
from google.protobuf import json_format, struct_pb2

from aion.core.a2a import Reply, ReplyError, reply_of
from aion.core.constants.a2a import (
    CARD_ACTION_EVENT_PAYLOAD_SCHEMA_V1,
    EVENT_EXTENSION_URI_V1,
    MESSAGE_EVENT_PAYLOAD_SCHEMA_V1,
    SOURCE_SYSTEM_EVENT_PAYLOAD_SCHEMA_V1,
)


def _data(value: Any, schema: str | None = None) -> Part:
    data = struct_pb2.Value()
    json_format.ParseDict(value, data)
    part = Part(data=data)
    if schema is not None:
        part.metadata.update({EVENT_EXTENSION_URI_V1: {"schema": schema}})
    return part


def _message(*parts: Part) -> Message:
    return Message(message_id="m-1", role=Role.ROLE_USER, parts=list(parts))


def _from_slack(text: str) -> Message:
    """A reply from a messaging distribution: the user's text and the payloads the distribution adds."""
    return _message(
        Part(text=text),
        _data({"userId": "U1", "contextId": "C1", "messageId": "M1"}, MESSAGE_EVENT_PAYLOAD_SCHEMA_V1),
        _data({"provider": "slack", "event": {"type": "message", "text": text}}, SOURCE_SYSTEM_EVENT_PAYLOAD_SCHEMA_V1),
    )


class TestValue:
    def test_text_is_the_value(self):
        assert reply_of(_message(Part(text="yes"))).value == "yes"

    def test_json_written_in_the_text_stays_text(self):
        for text in ('{"confirmed": true}', "42", "null"):
            assert reply_of(_message(Part(text=text))).value == text

    def test_text_parts_are_paragraphs(self):
        assert reply_of(_message(Part(text="first"), Part(text="second"))).value == "first\nsecond"

    @pytest.mark.parametrize("value", [{"decisions": [{"type": "approve"}]}, {}, ["a", "b"], "text", True, None])
    def test_a_data_part_is_the_value_whatever_the_text(self, value):
        reply = reply_of(_message(Part(text="ignored"), _data(value)))

        assert reply.has_data
        assert reply.value == value
        assert reply.text == "ignored"

    def test_the_parts_a_distribution_adds_are_not_the_reply(self):
        reply = reply_of(_from_slack("yes"))

        assert reply.value == "yes"
        assert not reply.has_data
        assert [part.text for part in reply.parts] == ["yes"]

    def test_a_pressed_card_button_is_its_action_id(self):
        click = _data({"userId": "U1", "contextId": "C1", "actionId": "approve"}, CARD_ACTION_EVENT_PAYLOAD_SCHEMA_V1)

        reply = reply_of(_message(click))

        assert (reply.value, reply.action_id, reply.parts) == ("approve", "approve", ())

    def test_data_of_its_own_comes_before_a_pressed_button(self):
        click = _data({"userId": "U1", "contextId": "C1", "actionId": "approve"}, CARD_ACTION_EVENT_PAYLOAD_SCHEMA_V1)

        assert reply_of(_message(click, _data({"choice": "later"}))).value == {"choice": "later"}

    def test_no_message_is_an_empty_reply(self):
        assert reply_of(None) == Reply()
        assert reply_of(None).value == ""


class TestFiles:
    def test_files_come_with_the_reply_in_order(self):
        stored = Part(url="https://files.test/a.pdf", media_type="application/pdf")
        inline = Part(raw=b"png", media_type="image/png")

        reply = reply_of(_message(stored, Part(text="both"), inline))

        assert reply.files == (stored, inline)
        assert reply.parts == (stored, Part(text="both"), inline)
        assert reply.value == "both"


class TestSeveralDataParts:
    def test_more_than_one_data_part_of_its_own_is_no_reply(self):
        with pytest.raises(ReplyError, match="at most one data part of its own; this one has 2"):
            reply_of(_message(_data({"a": 1}), _data({"b": 2})))

    def test_the_parts_a_distribution_adds_do_not_count(self):
        message = _from_slack("yes")
        message.parts.append(_data({"choice": "now"}))

        assert reply_of(message).value == {"choice": "now"}

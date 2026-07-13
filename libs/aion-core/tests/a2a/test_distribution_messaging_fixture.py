"""Cross-language fixtures for Distribution/Messaging 1.0.0."""

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from aion.core.a2a.extensions.messaging import (
    CommandEventPayload,
    MessageActionPayload,
    MessageEventPayload,
    ReactionActionPayload,
    ReactionEventPayload,
    SourceSystemEventPayload,
)
from aion.core.constants.a2a import (
    COMMAND_EVENT_PAYLOAD_SCHEMA_V1,
    COMMAND_EVENT_TYPE_V1,
    DISTRIBUTION_EXTENSION_URI_V1,
    EVENT_EXTENSION_URI_V1,
    MESSAGE_ACTION_PAYLOAD_SCHEMA_V1,
    MESSAGE_EVENT_PAYLOAD_SCHEMA_V1,
    MESSAGE_EVENT_TYPE_V1,
    MESSAGING_EXTENSION_URI_V1,
    REACTION_ACTION_PAYLOAD_SCHEMA_V1,
    REACTION_EVENT_PAYLOAD_SCHEMA_V1,
    REACTION_EVENT_TYPE_V1,
    SOURCE_SYSTEM_EVENT_PAYLOAD_SCHEMA_V1,
    STREAM_DELTA_PAYLOAD_SCHEMA_V1,
)


FIXTURE_PATH = (
    Path(__file__).parents[1]
    / "fixtures"
    / "a2a"
    / "distribution-messaging-1.0.0.json"
)


@pytest.fixture(scope="module")
def messaging_fixture() -> dict:
    """Load the canonical cross-language messaging fixture.

    Returns:
        The decoded Distribution/Messaging fixture object.
    """
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_canonical_constants_match_fixture(messaging_fixture: dict) -> None:
    """Python constants match the canonical URIs and event declarations."""
    assert messaging_fixture["extensionUri"] == MESSAGING_EXTENSION_URI_V1
    assert messaging_fixture["eventUri"] == EVENT_EXTENSION_URI_V1
    assert messaging_fixture["eventTypes"] == {
        "message": MESSAGE_EVENT_TYPE_V1,
        "reaction": REACTION_EVENT_TYPE_V1,
        "command": COMMAND_EVENT_TYPE_V1,
    }
    assert messaging_fixture["schemaUris"] == {
        "messageEvent": MESSAGE_EVENT_PAYLOAD_SCHEMA_V1,
        "reactionEvent": REACTION_EVENT_PAYLOAD_SCHEMA_V1,
        "commandEvent": COMMAND_EVENT_PAYLOAD_SCHEMA_V1,
        "sourceSystemEvent": SOURCE_SYSTEM_EVENT_PAYLOAD_SCHEMA_V1,
        "messageAction": MESSAGE_ACTION_PAYLOAD_SCHEMA_V1,
        "reactionAction": REACTION_ACTION_PAYLOAD_SCHEMA_V1,
        "streamDelta": STREAM_DELTA_PAYLOAD_SCHEMA_V1,
    }
    assert messaging_fixture["messageExtensions"] == [
        DISTRIBUTION_EXTENSION_URI_V1,
        EVENT_EXTENSION_URI_V1,
        MESSAGING_EXTENSION_URI_V1,
    ]


@pytest.mark.parametrize(
    ("name", "parent_context_id", "reply_to_message_id"),
    [
        ("root", None, None),
        ("nested", "context-room", None),
        ("directReply", None, "message-100"),
        ("nestedReply", "context-room", "message-101"),
    ],
)
def test_message_context_shapes_round_trip(
    messaging_fixture: dict,
    name: str,
    parent_context_id: str | None,
    reply_to_message_id: str | None,
) -> None:
    """Hierarchy and direct-reply fields remain independent on the wire."""
    data = messaging_fixture["messageEventPayloads"][name]

    payload = MessageEventPayload.model_validate(data)

    assert payload.parent_context_id == parent_context_id
    assert payload.reply_to_message_id == reply_to_message_id
    assert payload.model_dump(by_alias=True, exclude_none=True) == data


@pytest.mark.parametrize(
    ("field", "model"),
    [
        ("reactionEventPayload", ReactionEventPayload),
        ("commandEventPayload", CommandEventPayload),
        ("sourceSystemEventPayload", SourceSystemEventPayload),
        ("messageActionPayload", MessageActionPayload),
        ("reactionActionPayload", ReactionActionPayload),
    ],
)
def test_other_payloads_round_trip_with_canonical_aliases(
    messaging_fixture: dict,
    field: str,
    model: type[BaseModel],
) -> None:
    """Event and action payloads use the same field aliases as Scala."""
    data = messaging_fixture[field]

    payload = model.model_validate(data)

    assert payload.model_dump(by_alias=True, exclude_none=True) == data


def test_source_payload_preserves_unknown_provider_json(
    messaging_fixture: dict,
) -> None:
    """Source events retain provider fields unknown to the normalized model."""
    data = messaging_fixture["sourceSystemEventPayload"]

    payload = SourceSystemEventPayload.model_validate(data)

    assert payload.event["unknown"] == {"preserved": True}

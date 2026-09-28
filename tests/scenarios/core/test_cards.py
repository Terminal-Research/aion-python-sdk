"""A card the agent posts, from its Card object to the client's part.

A card is not text: the agent hands the thread a ``Card``, and the server
puts the document on the wire as a file part of its own media type, tagged
with the schema the Cards extension defines and announced by the extension
URI on the message. The wire form is the contract - a client renders what it
recognizes - so the assertions here are about the part, not about the prose
that follows it.

The document itself comes from ``commands.card_jsx()``, which the agent
passes through unchanged, so a byte that differs at the client is a byte the
SDK changed.
"""

from __future__ import annotations

from typing import Optional

import pytest
from a2a.types.a2a_pb2 import Part, Task

from tests.scenarios.commands import CARD_TITLE, card_jsx, card_text
from tests.scenarios.harness import CARDS_EXTENSION_URI, Ev, ScenarioClient, final_task, reply_texts

pytestmark = [pytest.mark.artifacts]

CARD_MEDIA_TYPE = "application/vnd.aion.card+jsx"
"""Media type of a card document, as the Cards extension defines it."""

CARD_PAYLOAD_SCHEMA = f"{CARDS_EXTENSION_URI}#CardPayload"
"""Schema marker the part carries, under the extension URI."""

CARD_FILENAME = "scenario-card.card.jsx"
"""File name the SDK derives from the card's title."""


def card_event(events: list[Ev]) -> Ev:
    """The status update carrying the card, or a failure naming what did arrive."""
    for event in events:
        if event.kind != "status" or not event.raw.status.HasField("message"):
            continue
        if any(part.media_type == CARD_MEDIA_TYPE for part in event.raw.status.message.parts):
            return event
    raise AssertionError(f"no event carried a card part: {events}")


def card_part(parts) -> Optional[Part]:
    """The one card part of a message, or None when it carries none."""
    return next((part for part in parts if part.media_type == CARD_MEDIA_TYPE), None)


def stored_card(task: Task) -> Optional[Part]:
    """The card part the stored task kept, or None."""
    for message in task.history:
        part = card_part(message.parts)
        if part is not None:
            return part
    if task.status.HasField("message"):
        return card_part(task.status.message.parts)
    return None


@pytest.mark.command("card")
async def test_a_card_arrives_as_a_card_part(client: ScenarioClient) -> None:
    """The card travels as a file part of the card media type, not as text."""
    events = await client.send("card")

    part = card_part(card_event(events).raw.status.message.parts)

    assert part is not None
    assert part.WhichOneof("content") == "raw", "the card did not arrive as file content"
    assert part.media_type == CARD_MEDIA_TYPE
    assert part.filename == CARD_FILENAME, (
        f"the file name the SDK derived from {CARD_TITLE!r} changed"
    )


@pytest.mark.command("card")
async def test_the_card_is_tagged_with_its_schema_and_extension(client: ScenarioClient) -> None:
    """A client knows what it received: the schema on the part, the URI on the message."""
    events = await client.send("card")

    message = card_event(events).raw.status.message
    part = card_part(message.parts)

    assert dict(part.metadata)[CARDS_EXTENSION_URI] == {"schema": CARD_PAYLOAD_SCHEMA}
    assert CARDS_EXTENSION_URI in list(message.extensions)


@pytest.mark.command("card")
async def test_the_card_document_arrives_unchanged(client: ScenarioClient) -> None:
    """Byte for byte what the agent handed over, and the reply follows it."""
    events = await client.send("card")

    part = card_part(card_event(events).raw.status.message.parts)

    assert part.raw.decode("utf-8") == card_jsx()
    assert reply_texts(events) == [card_text()]
    assert final_task(events).state == "COMPLETED"


@pytest.mark.command("card")
async def test_the_stored_task_keeps_the_card(client: ScenarioClient) -> None:
    """A card is durable: a later tasks/get holds the same document and tags."""
    events = await client.send("card")

    stored = await client.get_task(final_task(events).task_id)
    part = stored_card(stored)

    assert part is not None, f"the stored task kept no card: {stored}"
    assert part.raw.decode("utf-8") == card_jsx()
    assert part.media_type == CARD_MEDIA_TYPE
    assert dict(part.metadata)[CARDS_EXTENSION_URI] == {"schema": CARD_PAYLOAD_SCHEMA}

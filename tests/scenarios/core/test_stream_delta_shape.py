"""The stream-delta channel has the shape the messaging extension specifies.

The extension defines two separate things and a client needs both. The
artifact id says *which channel* an update belongs to - ``aion:stream-delta``
is the model's streamed answer rather than a file the agent produced. The
schema marker on the artifact update's own metadata says *what shape* the
payload in it has - ``StreamDeltaPayload``. Neither answers the other's
question, and the spec puts them in different places for that reason.

A section is a run of chunks closed by one update with the section's whole
text: ``append`` unset, ``lastChunk`` set. These run against a real server
for both frameworks, so they are also what says the two adapters agree: a
client written against one of them reads the other without knowing which it
is talking to.
"""

from __future__ import annotations

import pytest

from aion.core.constants import MESSAGING_EXTENSION_URI_V1, STREAM_DELTA_PAYLOAD_SCHEMA_V1
from tests.scenarios.commands import stream_text
from tests.scenarios.harness import (
    STREAM_DELTA_ARTIFACT_ID,
    ScenarioClient,
    is_chunk,
    is_section_end,
)

pytestmark = pytest.mark.streaming


def _stream_updates(events):
    return [event for event in events if is_chunk(event) or is_section_end(event)]


@pytest.mark.command("stream")
async def test_every_update_names_the_channel_by_artifact_id(client: ScenarioClient) -> None:
    """The reserved id is what makes an update recognisable as the streamed answer."""
    events = await client.send("stream 3")

    updates = [event for event in events if event.kind == "artifact"]
    assert len(updates) == 4
    assert {event.artifact_id for event in updates} == {STREAM_DELTA_ARTIFACT_ID}


@pytest.mark.command("stream")
async def test_every_update_carries_the_schema_marker_on_the_event(client: ScenarioClient) -> None:
    """The marker sits on the artifact update, which is where the spec puts it.

    Asserted on every update, every chunk and the section's end included:
    a client that has to look inside the sequence to find out
    what it is reading cannot start reading at the first event.
    """
    events = await client.send("stream 5")

    updates = _stream_updates(events)
    assert updates
    for update in updates:
        marker = update.metadata.get(MESSAGING_EXTENSION_URI_V1)
        assert marker is not None, f"no messaging-extension marker on {update!r}"
        assert marker.get("schema") == STREAM_DELTA_PAYLOAD_SCHEMA_V1


@pytest.mark.command("stream")
async def test_the_artifact_carries_no_status_and_no_schema(client: ScenarioClient) -> None:
    """The schema belongs to the event; where a section ends, ``lastChunk`` says, not the artifact's metadata."""
    events = await client.send("stream 3")

    for update in _stream_updates(events):
        assert "status" not in update.artifact_metadata
        assert "status_reason" not in update.artifact_metadata
        assert MESSAGING_EXTENSION_URI_V1 not in update.artifact_metadata


@pytest.mark.command("stream")
async def test_the_section_is_closed_by_the_end_mark(client: ScenarioClient) -> None:
    """Only the update that ends the section has `lastChunk`: one empty part, appended.

    The text is never sent twice, so a client that appends every update
    holds the answer exactly once. Nothing but the terminal task follows it.
    """
    events = await client.send("stream 3")

    updates = _stream_updates(events)
    assert [update.last_chunk for update in updates] == [False, False, False, True]
    end = updates[-1]
    assert end.append is True
    assert end.texts == ("",)
    assert "".join(update.text for update in updates) == stream_text(3)

    after = events[events.index(end) + 1:]
    assert len(after) == 1 and after[0].kind == "task" and after[0].final, after


@pytest.mark.command("stream")
async def test_the_first_chunk_opens_a_section_and_every_chunk_carries_content(
    client: ScenarioClient,
) -> None:
    """A reader appends from the second chunk on, and never receives an empty one.

    ``append`` is how a client knows whether a chunk starts a new section of
    the answer or continues the open one, so the first must say false and the
    rest true. And an empty chunk would tell a client rendering as it reads
    nothing at all, so only the end mark that closes the section is empty.
    """
    events = await client.send("stream 3")

    chunks = [event for event in events if is_chunk(event)]
    assert [chunk.append for chunk in chunks] == [False, True, True]
    assert all(chunk.text for chunk in chunks)


@pytest.mark.command("stream")
async def test_a_single_chunk_is_still_the_opening_of_a_section(client: ScenarioClient) -> None:
    """The one-chunk answer is the edge case the append rule is easiest to lose on.

    An answer that fits in one chunk still opens a section, so that flag is
    false there too. Asserting the shape without it would pass on an adapter
    that appended a lone chunk onto whatever the client had open before.
    """
    events = await client.send("stream 1")

    chunks = [event for event in events if is_chunk(event)]
    assert len(chunks) == 1
    assert chunks[0].append is False
    assert chunks[0].text

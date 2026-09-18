"""The stream-delta channel has the shape the messaging extension specifies.

The extension defines two separate things and a client needs both. The
artifact id says *which channel* an update belongs to - ``aion:stream-delta``
is the live text of a reply rather than a file the agent produced. The schema
marker on the artifact update's own metadata says *what shape* the payload in
it has - ``StreamDeltaPayload``. Neither answers the other's question, and the
spec puts them in different places for that reason.

These run against a real server for both frameworks, so they are also what
says the two adapters agree: a client written against one of them reads the
other without knowing which it is talking to.
"""

from __future__ import annotations

import pytest

from aion.core.constants import MESSAGING_EXTENSION_URI_V1, STREAM_DELTA_PAYLOAD_SCHEMA_V1
from tests.scenarios.harness import (
    STREAM_DELTA_ARTIFACT_ID,
    ScenarioClient,
    is_chunk,
)

pytestmark = pytest.mark.streaming


@pytest.mark.command("stream")
async def test_every_delta_names_the_channel_by_artifact_id(client: ScenarioClient) -> None:
    """The reserved id is what makes a delta recognisable as live reply text."""
    events = await client.send("stream 3")

    deltas = [event for event in events if is_chunk(event)]
    assert len(deltas) == 3
    assert {event.artifact_id for event in deltas} == {STREAM_DELTA_ARTIFACT_ID}


@pytest.mark.command("stream")
async def test_every_delta_carries_the_schema_marker_on_the_event(client: ScenarioClient) -> None:
    """The marker sits on the artifact update, which is where the spec puts it.

    Asserted on every delta rather than the first: a client that has to look
    inside the sequence to find out what it is reading cannot start reading at
    the first event.
    """
    events = await client.send("stream 3")

    deltas = [event for event in events if is_chunk(event)]
    assert deltas
    for delta in deltas:
        marker = delta.metadata.get(MESSAGING_EXTENSION_URI_V1)
        assert marker is not None, f"no messaging-extension marker on {delta!r}"
        assert marker.get("schema") == STREAM_DELTA_PAYLOAD_SCHEMA_V1


@pytest.mark.command("stream")
async def test_the_artifact_carries_its_own_status_not_the_schema(client: ScenarioClient) -> None:
    """Artifact metadata describes the artifact; the schema belongs to the event."""
    events = await client.send("stream 3")

    for delta in (event for event in events if is_chunk(event)):
        artifact_metadata = {
            key: value.decode() if isinstance(value, bytes) else value
            for key, value in _artifact_metadata(delta).items()
        }
        assert artifact_metadata.get("status") == "active"
        assert artifact_metadata.get("status_reason") == "chunk_streaming"
        assert MESSAGING_EXTENSION_URI_V1 not in artifact_metadata


@pytest.mark.command("stream")
async def test_the_sequence_is_closed_by_the_durable_reply_not_by_last_chunk(
    client: ScenarioClient,
) -> None:
    """`lastChunk` does not end a stream-delta sequence, and is not how to read one.

    Neither adapter can mark the closing delta, and for the same reason in
    different words. ADK's streaming contract is a run of `partial=True`
    responses followed by a separate `partial=False` response carrying the
    aggregated turn, so at the moment a partial is converted there is nothing
    that could say it was the last. LangGraph can set the flag, but only on a
    chunk that carries content, and langchain-core ends a stream with an empty
    `chunk_position="last"` chunk which produces no event at all.

    What does close the sequence is the durable message that follows it, and
    after that the terminal task. This test holds that to be the contract, so
    that a client is never written against a flag that does not arrive.
    """
    events = await client.send("stream 3")

    deltas = [event for event in events if is_chunk(event)]
    assert deltas
    assert not any(delta.last_chunk for delta in deltas)

    after = events[events.index(deltas[-1]) + 1:]
    assert any(event.kind in ("status", "message") and event.text for event in after), (
        "nothing durable followed the deltas, so the sequence never closed"
    )
    assert after[-1].kind == "task" and after[-1].final


def _artifact_metadata(event) -> dict:
    """Artifact-level metadata of a recorded artifact update.

    ``Ev`` projects the event's metadata, which is the one the schema marker
    lives on; the artifact's own is read off the protobuf it kept.
    """
    from google.protobuf.json_format import MessageToDict

    artifact = event.raw.artifact
    return MessageToDict(artifact).get("metadata", {})

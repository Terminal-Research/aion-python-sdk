"""Replies that arrive in pieces."""

from __future__ import annotations

import pytest

from tests.scenarios.commands import stream_chunks, stream_text
from tests.scenarios.harness import (
    ScenarioClient,
    assert_shape,
    chunks,
    final_task,
    reply_texts,
    status,
    task,
)

pytestmark = pytest.mark.streaming


@pytest.mark.command("stream")
async def test_a_chunked_reply_arrives_as_deltas_then_one_message(client: ScenarioClient) -> None:
    """Chunks arrive as stream-delta artifacts, then the whole reply as one message."""
    events = await client.send("stream 5")

    assert_shape(events, [
        task("SUBMITTED"),
        status("WORKING", text=""),
        chunks(5),
        status("WORKING", text=stream_text(5)),
        task("COMPLETED"),
    ])


@pytest.mark.command("stream")
async def test_the_chunks_are_the_message_broken_up(client: ScenarioClient) -> None:
    """Every chunk arrives once, in order, and they add up to the reply."""
    events = await client.send("stream 4")

    deltas = [event for event in events if event.kind == "artifact"]
    assert [event.text for event in deltas] == list(stream_chunks(4))
    assert reply_texts(events)[-1] == stream_text(4)
    assert final_task(events).text == stream_text(4)


@pytest.mark.command("stream")
async def test_the_first_chunk_opens_the_artifact_and_the_rest_append(client: ScenarioClient) -> None:
    """A chunked reply is one artifact: the first chunk opens it, the rest append."""
    events = await client.send("stream 3")

    deltas = [event for event in events if event.kind == "artifact"]
    assert [event.append for event in deltas] == [False, True, True]
    assert len({event.artifact_id for event in deltas}) == 1


@pytest.mark.command("stream")
async def test_one_chunk_is_still_a_chunked_reply(client: ScenarioClient) -> None:
    """`stream 1` keeps the same shape - one delta, then the message."""
    events = await client.send("stream 1")

    assert_shape(events, [
        task("SUBMITTED"),
        status("WORKING", text=""),
        chunks(1),
        status("WORKING", text=stream_text(1)),
        task("COMPLETED"),
    ])

"""Replies that arrive in pieces, and the indicator sent while one is built.

A chunked reply is one message the client sees grow. A typing indicator is
the other half of the same idea and the opposite of durable: it is delivered
live so that the caller knows the agent is busy, and it is never part of what
the turn leaves behind.
"""

from __future__ import annotations

import pytest

from tests.scenarios.commands import (
    TYPING_INDICATOR,
    stream_chunks,
    stream_text,
    typing_text,
)
from tests.scenarios.harness import (
    ScenarioClient,
    assert_shape,
    chunks,
    final_task,
    reply_texts,
    status,
    stored_texts,
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


# --------------------------------------------------------------------------
# The ephemeral indicator
# --------------------------------------------------------------------------

@pytest.mark.command("typing")
async def test_the_indicator_is_delivered_before_the_reply(client: ScenarioClient) -> None:
    """The caller learns the agent is busy, and the answer follows.

    Delivery is the guarantee both adapters hold, so this reads the texts the
    stream carried rather than the events that carried them: one adapter
    sends the indicator as an ephemeral artifact, the other as a status, and
    what a caller is owed here is the order.
    """
    events = await client.send("typing")

    delivered = [event.text for event in events if event.text]
    assert TYPING_INDICATOR in delivered, f"the indicator never arrived: {events}"
    assert typing_text() in delivered
    assert delivered.index(TYPING_INDICATOR) < delivered.index(typing_text())
    assert final_task(events).state == "COMPLETED"


@pytest.mark.command("typing")
async def test_the_indicator_is_not_a_durable_reply(client: ScenarioClient) -> None:
    """The turn has one durable reply, and the indicator is not it."""
    events = await client.send("typing")

    assert reply_texts(events) == [typing_text()]


@pytest.mark.command("typing")
async def test_the_indicator_never_reaches_task_history(client: ScenarioClient) -> None:
    """What was shown once is gone; what was said is kept."""
    events = await client.send("typing")

    stored = await client.get_task(final_task(events).task_id)
    texts = stored_texts(stored)

    assert typing_text() in texts
    assert TYPING_INDICATOR not in texts, (
        f"the ephemeral indicator was persisted into task history: {texts}"
    )

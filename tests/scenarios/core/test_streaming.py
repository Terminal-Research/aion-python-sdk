"""A streamed answer: chunks of a section, then the mark that ends it.

The model's streamed answer is the task's ``aion:stream-delta`` artifact. A
client sees it grow chunk by chunk - the first chunk of a section with
``append`` unset, the rest with it set - and the section ends with
``lastChunk`` set, here on an empty end mark, since the agent's iterator
does not say which chunk is its last. The text is never sent twice: the
task joins it into one part when it stops. The streamed text never becomes
a message. Every chunk goes out as the agent produced it.
"""

from __future__ import annotations

import pytest

from tests.scenarios.commands import stream_chunks, stream_text
from tests.scenarios.harness import (
    ASSISTANT_ANSWER_ARTIFACT_ID,
    STREAM_DELTA_ARTIFACT_ID,
    ScenarioClient,
    assert_shape,
    chunks,
    final_task,
    is_chunk,
    reply_texts,
    section_end,
    section_texts,
    status,
    stored_artifact,
    stored_texts,
    task,
)

pytestmark = pytest.mark.streaming


# --------------------------------------------------------------------------
# Every chunk, as the agent produced it
# --------------------------------------------------------------------------

@pytest.mark.command("stream")
async def test_a_streamed_answer_arrives_as_chunks_then_its_whole_text(
    client: ScenarioClient,
) -> None:
    """Chunks arrive as stream-delta updates, then the empty end mark."""
    events = await client.send("stream 5")

    assert_shape(events, [
        task("SUBMITTED"),
        status("WORKING", text=""),
        chunks(5),
        section_end(text=""),
        task("COMPLETED"),
    ])
    assert section_texts(events) == [stream_text(5)]


@pytest.mark.command("stream")
async def test_the_chunks_are_the_answer_broken_up(client: ScenarioClient) -> None:
    """Every chunk arrives once, in order, and they add up to the section's whole text."""
    events = await client.send("stream 4")

    assert [event.text for event in events if is_chunk(event)] == list(stream_chunks(4))
    assert section_texts(events) == [stream_text(4)]


@pytest.mark.command("stream")
async def test_the_first_chunk_opens_the_section_and_the_rest_append(
    client: ScenarioClient,
) -> None:
    """One artifact: the first chunk opens it, the rest append, the end mark appends nothing and ends it."""
    events = await client.send("stream 3")

    updates = [event for event in events if event.kind == "artifact"]
    assert [(event.append, event.last_chunk) for event in updates] == [
        (False, False),
        (True, False),
        (True, False),
        (True, True),
    ]
    assert {event.artifact_id for event in updates} == {STREAM_DELTA_ARTIFACT_ID}


@pytest.mark.command("stream")
async def test_one_chunk_is_still_a_section(client: ScenarioClient) -> None:
    """`stream 1` keeps the same shape: one chunk, then the end mark."""
    events = await client.send("stream 1")

    assert_shape(events, [
        task("SUBMITTED"),
        status("WORKING", text=""),
        chunks(1),
        section_end(text=""),
        task("COMPLETED"),
    ])
    assert section_texts(events) == [stream_text(1)]


# --------------------------------------------------------------------------
# What the task keeps
# --------------------------------------------------------------------------

@pytest.mark.command("stream")
async def test_the_streamed_answer_is_not_a_message(client: ScenarioClient) -> None:
    """No status carries the streamed text, and the task's history holds only the caller's message."""
    events = await client.send("stream 3")

    assert reply_texts(events) == []
    closing = final_task(events)
    assert closing.text == ""
    stored = await client.get_task(closing.task_id)
    assert stored_texts(stored) == ["stream 3"]


@pytest.mark.command("stream")
async def test_the_task_keeps_the_whole_text_of_the_section(client: ScenarioClient) -> None:
    """The stored stream-delta artifact is the section's whole text joined into one part, and it is the task's result.

    A streamed answer is a result of the task, so the task gets no
    ``aion:assistant-answer`` beside it.
    """
    events = await client.send("stream 3")
    closing = final_task(events)

    stored = await client.get_task(closing.task_id)
    for where, holder in (("tasks/get", stored), ("the closing Task", closing.raw)):
        streamed = stored_artifact(holder, STREAM_DELTA_ARTIFACT_ID)
        assert streamed is not None, f"{where}: no streamed answer among {list(holder.artifacts)}"
        assert [part.text for part in streamed.parts] == [stream_text(3)], where
        assert stored_artifact(holder, ASSISTANT_ANSWER_ARTIFACT_ID) is None, where


# --------------------------------------------------------------------------
# A unary send
# --------------------------------------------------------------------------


@pytest.mark.command("echo")
async def test_a_unary_send_answers_with_the_completed_task(client: ScenarioClient) -> None:
    """`SendMessage` without a stream answers once: the task, completed, the reply on its status."""
    events = await client.send("echo unary hello", stream=False)

    [answer] = events
    assert answer.kind == "task"
    assert answer.state == "COMPLETED"
    stored = await client.get_task(answer.task_id)
    assert "unary hello" in stored_texts(stored)

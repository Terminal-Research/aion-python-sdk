"""The answer of a task that only talked: the ``aion:assistant-answer`` artifact.

A client reads what a task produced from its artifacts. A task that
completes without any result - no artifact of its own, no streamed answer -
gets ``aion:assistant-answer`` right before ``COMPLETED``: every part of the
last message the agent sent in the completing turn, in one update with
``append`` unset and ``lastChunk`` set. The message stays in the history as
well. The agent's thoughts and reactions are not results, so a task with
only those still gets it; a task that fails or is cancelled never does.
"""

from __future__ import annotations

import pytest

from tests.scenarios.commands import (
    FAIL_REPLY_TEXT,
    SEND_FILE_BYTES,
    SEND_FILE_TEXT,
    ask_answer_text,
    artifacts_text,
    ask_question,
    echo_text,
    step_text,
)
from tests.scenarios.harness import (
    ASSISTANT_ANSWER_ARTIFACT_ID,
    SKIP,
    ScenarioClient,
    answer,
    artifact_updates,
    assert_shape,
    final_task,
    replies,
    status,
    stored_artifact,
    stored_texts,
    task,
)

pytestmark = pytest.mark.artifacts


@pytest.mark.command("steps")
async def test_the_last_message_of_the_turn_is_the_answer(client: ScenarioClient) -> None:
    """Three messages, one answer: the last of them, right before COMPLETED."""
    events = await client.send("steps 3")

    assert_shape(events, [
        SKIP,
        status("WORKING", text=step_text(3, 3)),
        answer(text=step_text(3, 3)),
        task("COMPLETED"),
    ])
    assert len(artifact_updates(events, ASSISTANT_ANSWER_ARTIFACT_ID)) == 1


@pytest.mark.command("echo")
async def test_the_task_keeps_the_answer_and_the_message(client: ScenarioClient) -> None:
    """The stored task holds the answer as its artifact and the reply in its history."""
    events = await client.send("echo kept twice")
    closing = final_task(events)

    stored = await client.get_task(closing.task_id)
    assert echo_text("kept twice") in stored_texts(stored)
    for where, holder in (("tasks/get", stored), ("the closing Task", closing.raw)):
        kept = stored_artifact(holder, ASSISTANT_ANSWER_ARTIFACT_ID)
        assert kept is not None, f"{where}: no answer among {list(holder.artifacts)}"
        assert [part.text for part in kept.parts] == [echo_text("kept twice")], where


@pytest.mark.command("send-file")
async def test_the_answer_carries_every_part_of_the_message(client: ScenarioClient) -> None:
    """A reply of text and a file becomes an answer of the same two parts."""
    events = await client.send("send-file reply")

    *_, reply = replies(events)
    (update,) = artifact_updates(events, ASSISTANT_ANSWER_ARTIFACT_ID)
    assert list(update.raw.artifact.parts) == list(reply.raw.status.message.parts)
    text, file = update.raw.artifact.parts
    assert text.text == SEND_FILE_TEXT
    assert file.raw == SEND_FILE_BYTES


@pytest.mark.command("artifacts")
async def test_a_task_with_an_artifact_of_its_own_gets_no_answer(client: ScenarioClient) -> None:
    """The artifacts the agent emitted are the task's result, so its reply stays a message only."""
    events = await client.send("artifacts")

    assert artifacts_text() in [event.text for event in replies(events)]
    assert artifact_updates(events, ASSISTANT_ANSWER_ARTIFACT_ID) == []
    stored = await client.get_task(final_task(events).task_id)
    assert stored_artifact(stored, ASSISTANT_ANSWER_ARTIFACT_ID) is None


@pytest.mark.command("fail")
async def test_a_failed_task_gets_no_answer(client: ScenarioClient) -> None:
    """What the agent said before failing is a message of a failed turn, not its answer."""
    events = await client.send("fail after-reply")

    assert FAIL_REPLY_TEXT in [event.text for event in replies(events)]
    assert final_task(events).state == "FAILED"
    assert artifact_updates(events, ASSISTANT_ANSWER_ARTIFACT_ID) == []
    stored = await client.get_task(final_task(events).task_id)
    assert stored_artifact(stored, ASSISTANT_ANSWER_ARTIFACT_ID) is None


@pytest.mark.command("ask")
async def test_the_answer_comes_from_the_turn_that_completes(client: ScenarioClient) -> None:
    """A question that pauses the task is not its answer; the reply of the completing turn is."""
    asked = await client.send("ask")
    paused = final_task(asked)
    assert paused.state == "INPUT_REQUIRED"
    assert ask_question() in paused.text
    assert artifact_updates(asked, ASSISTANT_ANSWER_ARTIFACT_ID) == []

    resumed = await client.send("blue", task_id=paused.task_id, context_id=paused.context_id)

    assert_shape(resumed, [SKIP, answer(text=ask_answer_text("blue")), task("COMPLETED")])
    stored = await client.get_task(paused.task_id)
    kept = stored_artifact(stored, ASSISTANT_ANSWER_ARTIFACT_ID)
    assert kept is not None
    assert [part.text for part in kept.parts] == [ask_answer_text("blue")]

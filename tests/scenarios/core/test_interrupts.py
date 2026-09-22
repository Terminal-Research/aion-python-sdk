"""Interrupt pauses a task for user input; resume delivers the answer.

The ``ask`` command calls ``interrupt()`` inside a LangGraph node. The server
converts it to INPUT_REQUIRED on the wire, and the client resumes by sending
a new message with the same ``task_id``. The agent then quotes the answer.

``ask-twice`` is the same thing twice over, which is where a resume can go
wrong without anyone noticing: one task has to pause, resume, pause again and
resume again, and both answers have to reach the agent in the order they were
given.

ADK has no interrupt primitive, so these scenarios run on LangGraph only.
"""

from __future__ import annotations

import pytest
from a2a.types import TaskState

from tests.scenarios.commands import (
    ask_answer_text,
    ask_question,
    ask_twice_answer_text,
    ask_twice_questions,
)
from tests.scenarios.harness import (
    ScenarioClient,
    assert_shape,
    final_task,
    reply_texts,
    status,
    stored_texts,
    task,
)

pytestmark = [pytest.mark.interrupts]

RESUME_ANSWER = "use Python"
FIRST_ANSWER = "the first one"
SECOND_ANSWER = "yes, quite sure"


@pytest.mark.command("ask")
async def test_ask_pauses_the_task_as_input_required(client: ScenarioClient) -> None:
    """The stream ends with INPUT_REQUIRED carrying the interrupt question."""
    events = await client.send("ask")
    closing = final_task(events)
    assert closing.state == "INPUT_REQUIRED"
    assert ask_question() in closing.text


@pytest.mark.command("ask")
async def test_resume_delivers_the_answer_and_completes(client: ScenarioClient) -> None:
    """Sending a message to the paused task resumes it with the user's answer."""
    events = await client.send("ask")
    closing = final_task(events)
    task_id = closing.task_id
    context_id = closing.context_id

    resume_events = await client.send(
        RESUME_ANSWER, task_id=task_id, context_id=context_id
    )
    resume_closing = final_task(resume_events)

    assert resume_closing.state == "COMPLETED"
    assert ask_answer_text(RESUME_ANSWER) in reply_texts(resume_events)


@pytest.mark.command("ask")
async def test_resumed_task_is_stored_as_completed(client: ScenarioClient) -> None:
    """tasks/get after resume reports COMPLETED, not INPUT_REQUIRED."""
    events = await client.send("ask")
    closing = final_task(events)

    await client.send(
        RESUME_ANSWER, task_id=closing.task_id, context_id=closing.context_id
    )
    stored = await client.get_task(closing.task_id)

    assert stored.status.state == TaskState.TASK_STATE_COMPLETED


@pytest.mark.command("ask-twice")
async def test_two_interrupts_pause_the_same_task_in_turn(client: ScenarioClient) -> None:
    """Each interrupt pauses the one task again, with its own question."""
    first_question, second_question = ask_twice_questions()

    opened = final_task(await client.send("ask-twice"))
    assert opened.state == "INPUT_REQUIRED"
    assert first_question in opened.text

    paused_again = final_task(
        await client.send(FIRST_ANSWER, task_id=opened.task_id, context_id=opened.context_id)
    )

    assert paused_again.state == "INPUT_REQUIRED"
    assert second_question in paused_again.text
    assert paused_again.task_id == opened.task_id
    assert paused_again.context_id == opened.context_id


@pytest.mark.command("ask-twice")
async def test_both_answers_reach_the_agent_in_order(client: ScenarioClient) -> None:
    """The second resume completes the task, carrying both answers as given."""
    opened = final_task(await client.send("ask-twice"))
    await client.send(FIRST_ANSWER, task_id=opened.task_id, context_id=opened.context_id)

    resumed = await client.send(
        SECOND_ANSWER, task_id=opened.task_id, context_id=opened.context_id
    )

    closing = final_task(resumed)
    assert closing.state == "COMPLETED"
    assert closing.task_id == opened.task_id
    assert ask_twice_answer_text(FIRST_ANSWER, SECOND_ANSWER) in reply_texts(resumed)


@pytest.mark.command("ask-twice")
async def test_a_twice_resumed_task_is_stored_as_completed(client: ScenarioClient) -> None:
    """The task the server kept agrees: two pauses, one outcome."""
    opened = final_task(await client.send("ask-twice"))
    await client.send(FIRST_ANSWER, task_id=opened.task_id, context_id=opened.context_id)
    await client.send(SECOND_ANSWER, task_id=opened.task_id, context_id=opened.context_id)

    stored = await client.get_task(opened.task_id)

    assert stored.status.state == TaskState.TASK_STATE_COMPLETED
    texts = stored_texts(stored)
    assert FIRST_ANSWER in texts
    assert SECOND_ANSWER in texts
    assert texts.index(FIRST_ANSWER) < texts.index(SECOND_ANSWER)
    assert ask_twice_answer_text(FIRST_ANSWER, SECOND_ANSWER) in texts

"""Interrupt pauses a task for user input; resume delivers the answer.

The ``ask`` command calls ``interrupt()`` inside a LangGraph node. The server
converts it to INPUT_REQUIRED on the wire, and the client resumes by sending
a new message with the same ``task_id``. The agent then quotes the answer.

ADK has no interrupt primitive, so these scenarios run on LangGraph only.
"""

from __future__ import annotations

import pytest

from tests.scenarios.commands import ask_answer_text, ask_question
from tests.scenarios.harness import ScenarioClient, assert_shape, final_task, reply_texts, status, task

pytestmark = [pytest.mark.interrupts]

RESUME_ANSWER = "use Python"


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

    from a2a.types import TaskState
    assert stored.status.state == TaskState.TASK_STATE_COMPLETED

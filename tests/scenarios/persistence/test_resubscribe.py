"""Resubscribing to a paused task where ownership is enforced.

The same request, ``tasks/resubscribe``, answers a task waiting for input in
two different ways, and which one a caller gets is decided by the store
behind the server rather than by the protocol.

Here - a real database, so a real ownership provider - a paused task has no
execution any process may join: the one that paused it released its claim on
purpose, and the resume will acquire a new one, on whichever instance
receives it. So the server replays the stored ``Task`` and closes the stream,
without building an execution that could never finish. The counterpart on the
default in-memory deployment, where one process both holds the pause and
carries the next turn, is ``core/test_resubscribe.py``.

Both are current behaviour and neither is the general rule. Nothing here is
an argument for making them the same.

Only LangGraph: ADK has no interrupt, and ``frameworks.UNSUPPORTED`` says so.
"""

from __future__ import annotations

import pytest
from a2a.types import TaskState

from tests.scenarios.commands import ask_answer_text, ask_question
from tests.scenarios.harness import ScenarioClient, final_task, reply_texts, stored_texts

pytestmark = [pytest.mark.persistence]

RESUME_ANSWER = "answered after the replay"


@pytest.mark.command("ask")
async def test_a_paused_task_is_replayed_once_and_the_stream_closes(
    pg_client: ScenarioClient,
) -> None:
    """One stored Task, carrying the question, and then the end of the stream."""
    opened = final_task(await pg_client.send("ask"))
    assert opened.state == "INPUT_REQUIRED"

    resubscribed = await pg_client.subscribe(opened.task_id)

    assert [event.kind for event in resubscribed] == ["task"], (
        f"the subscription delivered more than the stored task: {resubscribed}"
    )
    assert resubscribed[0].task_id == opened.task_id
    assert resubscribed[0].state == "INPUT_REQUIRED"
    assert ask_question() in resubscribed[0].text


@pytest.mark.command("ask")
async def test_the_replay_leaves_the_pause_as_it_found_it(
    pg_client: ScenarioClient,
) -> None:
    """No execution was started, so the task is still waiting and still resumable.

    The replay consuming the pause would be invisible in the subscription
    itself and obvious one message later, which is where this looks: the
    answer sent afterwards still reaches the agent, and the turn it was
    waiting to finish finishes.
    """
    opened = final_task(await pg_client.send("ask"))

    await pg_client.subscribe(opened.task_id)

    waiting = await pg_client.get_task(opened.task_id)
    assert waiting.status.state == TaskState.TASK_STATE_INPUT_REQUIRED

    resumed = await pg_client.send(
        RESUME_ANSWER, task_id=opened.task_id, context_id=opened.context_id
    )

    closing = final_task(resumed)
    assert closing.task_id == opened.task_id
    assert closing.state == "COMPLETED"
    assert ask_answer_text(RESUME_ANSWER) in reply_texts(resumed)
    assert [task.id for task in await pg_client.tasks_in_context(opened.context_id)] == [
        opened.task_id
    ]


@pytest.mark.command("ask")
async def test_a_settled_task_is_replayed_the_same_way(pg_client: ScenarioClient) -> None:
    """Once the turn is over, the replay is the outcome - question and answer kept."""
    opened = final_task(await pg_client.send("ask"))
    await pg_client.send(RESUME_ANSWER, task_id=opened.task_id, context_id=opened.context_id)

    resubscribed = await pg_client.subscribe(opened.task_id)

    assert [event.kind for event in resubscribed] == ["task"]
    assert resubscribed[0].state == "COMPLETED"
    texts = stored_texts(resubscribed[0].raw)
    assert ask_question() in texts
    assert RESUME_ANSWER in texts

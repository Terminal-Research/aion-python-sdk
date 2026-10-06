"""Resubscribing to a paused task in a2a-sdk's cluster mode.

Here - a real database, so the server runs a2a-sdk's cluster mode - a paused
task has no execution to join: the one that paused it was closed on purpose,
and the resume starts a new one on whichever instance receives it. So the
subscriber is served the way a subscriber on another instance would be: the
stored ``Task`` first, then the task's journal, until the task stops again.
The resume a second client sends is what ends the subscription. The
counterpart on the default in-memory deployment, where one process both
holds the pause and carries the next turn, is ``core/test_resubscribe.py``;
the subscriber sees the same thing either way.

Only LangGraph: ADK has no interrupt, and ``frameworks.UNSUPPORTED`` says so.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest
from a2a.types import TaskState

from tests.scenarios.commands import ask_answer_text, ask_question
from tests.scenarios.harness import Ev, ScenarioClient, final_task, reply_texts, stored_texts
from tests.scenarios.harness.recorder import _payload_of, to_event

pytestmark = [pytest.mark.persistence]

RESUME_ANSWER = "answered under a subscriber"

RESUME_TIMEOUT_SECONDS = 60
"""A subscriber still waiting after this is not slow but stuck."""

UNSUPPORTED_OPERATION_CODE = -32004
"""What A2A answers a resubscription to a task that already has an outcome."""


async def next_event(stream: AsyncIterator) -> Ev:
    """The next event of a raw stream, projected."""
    return to_event(_payload_of(await anext(stream)))


async def drain(stream: AsyncIterator) -> list[Ev]:
    """Everything left on a raw stream, until the server closes it."""
    return [to_event(_payload_of(response)) async for response in stream]


@pytest.mark.command("ask")
async def test_a_paused_task_is_followed_until_the_resume_finishes_it(
    pg_client: ScenarioClient,
) -> None:
    """The stored pause first, then the turn that ends it, closed by a Task."""
    opened = final_task(await pg_client.send("ask"))
    assert opened.state == "INPUT_REQUIRED"

    stream = pg_client.subscribe_stream(opened.task_id)
    attached = await next_event(stream)

    assert attached.kind == "task"
    assert attached.task_id == opened.task_id
    assert attached.state == "INPUT_REQUIRED"
    assert ask_question() in attached.text, "the subscriber was not told what was asked"

    subscriber = asyncio.create_task(drain(stream))
    resumed = await pg_client.send(
        RESUME_ANSWER, task_id=opened.task_id, context_id=opened.context_id
    )
    delivered = await asyncio.wait_for(subscriber, timeout=RESUME_TIMEOUT_SECONDS)

    assert final_task(resumed).state == "COMPLETED"
    closing = final_task(delivered)
    assert closing.task_id == opened.task_id
    assert closing.state == "COMPLETED"
    assert ask_answer_text(RESUME_ANSWER) in stored_texts(closing.raw)


@pytest.mark.command("ask")
async def test_following_the_pause_leaves_one_task_and_its_question(
    pg_client: ScenarioClient,
) -> None:
    """No second task, and nothing the pause had recorded is lost."""
    opened = final_task(await pg_client.send("ask"))

    stream = pg_client.subscribe_stream(opened.task_id)
    await next_event(stream)
    subscriber = asyncio.create_task(drain(stream))
    resumed = await pg_client.send(
        RESUME_ANSWER, task_id=opened.task_id, context_id=opened.context_id
    )
    await asyncio.wait_for(subscriber, timeout=RESUME_TIMEOUT_SECONDS)

    assert ask_answer_text(RESUME_ANSWER) in reply_texts(resumed)
    stored = await pg_client.get_task(opened.task_id)
    texts = stored_texts(stored)
    assert stored.status.state == TaskState.TASK_STATE_COMPLETED
    assert ask_question() in texts, "the question the task paused on is gone"
    assert RESUME_ANSWER in texts
    assert [task.id for task in await pg_client.tasks_in_context(opened.context_id)] == [
        opened.task_id
    ]


@pytest.mark.command("ask")
async def test_a_finished_task_refuses_a_subscriber(pg_client: ScenarioClient) -> None:
    """Once the turn is over there is nothing to follow; tasks/get has the outcome."""
    opened = final_task(await pg_client.send("ask"))
    await pg_client.send(RESUME_ANSWER, task_id=opened.task_id, context_id=opened.context_id)

    response = await pg_client.rpc("SubscribeToTask", {"id": opened.task_id})

    assert "error" in response, f"a finished task accepted a subscription: {response}"
    assert response["error"]["code"] == UNSUPPORTED_OPERATION_CODE
    stored = await pg_client.get_task(opened.task_id)
    assert stored.status.state == TaskState.TASK_STATE_COMPLETED
    texts = stored_texts(stored)
    assert ask_question() in texts
    assert RESUME_ANSWER in texts

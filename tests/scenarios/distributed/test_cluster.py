"""Two servers of one agent over one database: a2a-sdk's cluster mode.

Every other scenario in the suite drives one server. These drive two - two
operating-system processes, two proxies, one PostgreSQL - because that is the
shape a deployment has and the only place cluster mode can be assembled
wrong. The integration suite proves the versioned store and the journal
inside one interpreter; what it cannot show is a task executed by one process
and followed, continued or read back by another.

The rules are a2a-sdk's. Any server executes any request it receives. A
server that is not running the task follows it from the journal. Writes are
ordered by the task's version, and a write that would land on a task that is
already terminal is dropped.
"""

from __future__ import annotations

import asyncio

import pytest
from a2a.types import TaskState

from tests.scenarios.commands import ask_answer_text, echo_text, slow_text
from tests.scenarios.harness import (
    ScenarioClient,
    final_task,
    reply_texts,
    run_until_working,
    stored_texts,
)
from tests.scenarios.harness.pg import task_version

pytestmark = [pytest.mark.distributed]

UNSUPPORTED_OPERATION_CODE = -32004
"""What A2A answers a resubscription to a task that already has an outcome."""

FOLLOW_SECONDS = 3
"""How long the followed task runs: long enough to subscribe while it does."""

RACE_SECONDS = 4
"""How long the first execution of a raced task sleeps before it writes again."""

ANSWER = "use Python"
"""What the second server is told to resume a paused task with."""


@pytest.mark.command("slow")
async def test_the_other_server_follows_a_running_task_to_its_end(
    clients: tuple[ScenarioClient, ScenarioClient],
) -> None:
    """A subscriber on the server not running the task gets its live tail.

    a2a-sdk serves it a snapshot and then the task's journal; the stream is
    opened and closed by a Task, as every stream of this server is.
    """
    first, second = clients

    task_id, _ = await run_until_working(first, f"slow {FOLLOW_SECONDS}")
    followed = await second.subscribe(task_id)

    assert followed[0].kind == "task", f"the stream did not open with the task: {followed}"
    assert followed[0].state == "WORKING"
    closing = followed[-1]
    assert closing.kind == "task" and closing.state == "COMPLETED", (
        f"the stream did not close with the finished task: {followed}"
    )
    assert slow_text(float(FOLLOW_SECONDS)) in stored_texts(closing.raw)


@pytest.mark.command("ask")
async def test_a_task_paused_on_one_server_is_resumed_on_the_other(
    clients: tuple[ScenarioClient, ScenarioClient],
) -> None:
    """The next turn runs wherever it arrives, from the stored task."""
    first, second = clients

    opened = final_task(await first.send("ask"))
    assert opened.state == "INPUT_REQUIRED"

    events = await second.send(ANSWER, task_id=opened.task_id, context_id=opened.context_id)

    assert final_task(events).state == "COMPLETED"
    assert ask_answer_text(ANSWER) in reply_texts(events)
    stored = await first.get_task(opened.task_id)
    assert stored.status.state == TaskState.TASK_STATE_COMPLETED


@pytest.mark.command("slow")
async def test_a_second_message_runs_on_the_other_server_and_the_first_outcome_wins(
    clients: tuple[ScenarioClient, ScenarioClient],
) -> None:
    """Nothing stops two executions of one task; the version decides what lands.

    The first server is still sleeping when the second server executes a new
    message for the same task and finishes it. When the first one wakes up,
    its reply is a write over a version that has moved on, onto a task that
    is now terminal: a2a-sdk drops it and stops that execution.
    """
    first, second = clients

    task_id, _ = await run_until_working(first, f"slow {RACE_SECONDS}")
    events = await second.send("echo continue-me", task_id=task_id)

    assert final_task(events).state == "COMPLETED"
    assert echo_text("continue-me") in reply_texts(events)

    await asyncio.sleep(RACE_SECONDS + 2)

    stored = await first.get_task(task_id)
    assert stored.status.state == TaskState.TASK_STATE_COMPLETED
    assert echo_text("continue-me") in stored_texts(stored)
    assert slow_text(float(RACE_SECONDS)) not in stored_texts(stored), (
        "the overtaken execution wrote its reply over the finished task"
    )


@pytest.mark.command("echo")
async def test_resubscribing_to_a_finished_task_is_refused(
    clients: tuple[ScenarioClient, ScenarioClient],
) -> None:
    """There is nothing left to stream, so a2a-sdk refuses the subscription."""
    first, second = clients

    task_id = final_task(await first.send("echo already-done")).task_id

    for client in (first, second):
        response = await client.rpc("SubscribeToTask", {"id": task_id})
        assert "error" in response, f"a finished task accepted a subscription: {response}"
        assert response["error"]["code"] == UNSUPPORTED_OPERATION_CODE


@pytest.mark.command("echo")
async def test_both_servers_read_the_same_finished_task(
    clients: tuple[ScenarioClient, ScenarioClient],
) -> None:
    """One database, one answer: the outcome does not depend on who is asked."""
    first, second = clients

    events = await first.send("echo two-servers-one-database")
    task_id = final_task(events).task_id

    here = await first.get_task(task_id)
    there = await second.get_task(task_id)

    assert here.status.state == TaskState.TASK_STATE_COMPLETED
    assert here == there, (
        f"the two servers disagree about the task.\non A:\n{here}\non B:\n{there}"
    )
    assert echo_text("two-servers-one-database") in stored_texts(there)
    assert await task_version(task_id), "the task was written without a version"

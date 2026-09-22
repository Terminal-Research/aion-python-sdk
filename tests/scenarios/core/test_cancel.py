"""Cancelling a task that is still working through the public A2A endpoint.

A ``tasks/cancel`` request is the only way an external caller can stop work
it no longer wants.  The guarantee: once the server has accepted the cancel,
the task reaches CANCELED (not COMPLETED, not stuck in WORKING forever), and
a later ``tasks/get`` agrees.

The ``slow`` command emits a WORKING status immediately, then sleeps.  The
cancel is sent on that status rather than after a delay, so the task is
genuinely occupied when it arrives, on a loaded machine as much as an idle
one.

What the scenarios do not look at is whether the agent's coroutine received a
``CancelledError``: from outside, an accepted cancel means no further work
and no contradicting outcome, and those are what is asserted.
"""

from __future__ import annotations

import asyncio

import pytest
from a2a.types import TaskState

from tests.scenarios.commands import slow_text
from tests.scenarios.harness import ScenarioClient, final_task, reply_texts, stored_texts
from tests.scenarios.harness.recorder import Ev, _payload_of, to_event

pytestmark = [pytest.mark.terminal_states]

SLOW_SECONDS = 30
"""Long enough that the cancel always lands mid-sleep."""

SHORT_SECONDS = 2
"""Short enough to wait out once, where the point is that nothing arrives."""


async def _send_and_cancel(
    client: ScenarioClient, slow_seconds: int = SLOW_SECONDS
) -> tuple[str, list[Ev], TaskState.ValueType]:
    """Start a slow task, cancel it once it reaches WORKING with text, collect events.

    Returns the task id, every event the stream delivered, and the state of
    the Task the cancel request itself answered with.
    """
    events: list[Ev] = []
    task_id: str | None = None
    cancel_state: TaskState.ValueType | None = None

    stream = client.stream(f"slow {slow_seconds}")
    async for response in stream:
        ev = to_event(_payload_of(response))
        events.append(ev)
        if ev.task_id and task_id is None:
            task_id = ev.task_id
        if ev.state == "WORKING" and ev.text and task_id:
            cancel_state = (await client.cancel(task_id)).status.state
            break

    assert task_id is not None, "stream did not carry a task id before WORKING"
    assert cancel_state is not None

    async for response in stream:
        events.append(to_event(_payload_of(response)))

    return task_id, events, cancel_state


@pytest.mark.command("slow")
async def test_cancel_stops_a_working_task(client: ScenarioClient) -> None:
    """A cancel request on a WORKING task lands it in CANCELED."""
    _, events, cancel_state = await _send_and_cancel(client)

    closing = final_task(events)
    assert closing.state == "CANCELED"
    assert closing.final, "the terminal event did not close the stream"
    # The answer to the cancel request is already the outcome, so a caller
    # that never reads the stream still learns what happened.
    assert cancel_state == TaskState.TASK_STATE_CANCELED


@pytest.mark.command("slow")
async def test_a_cancelled_task_does_not_also_complete(client: ScenarioClient) -> None:
    """Nothing in the stream claims the outcome the cancel replaced."""
    _, events, _ = await _send_and_cancel(client)

    assert [event.state for event in events if event.kind == "task"].count("COMPLETED") == 0
    assert slow_text(float(SLOW_SECONDS)) not in reply_texts(events)


@pytest.mark.command("slow")
async def test_cancelled_task_is_stored_as_cancelled(client: ScenarioClient) -> None:
    """tasks/get after the cancel also says CANCELED, not the pre-cancel state."""
    task_id, _, _ = await _send_and_cancel(client)

    stored = await client.get_task(task_id)
    assert stored.status.state == TaskState.TASK_STATE_CANCELED


@pytest.mark.command("slow")
async def test_no_answer_arrives_after_a_cancel(client: ScenarioClient) -> None:
    """The work stops: the reply the agent would have sent never lands.

    The one place a deadline is the instrument rather than a substitute for
    one - an absence has no event to wait for. The agent was asked to answer
    after ``SHORT_SECONDS``; this waits past that moment and reads the task
    the server kept, which by then would carry the answer if the run had
    continued behind the cancel.
    """
    task_id, _, _ = await _send_and_cancel(client, SHORT_SECONDS)

    await asyncio.sleep(SHORT_SECONDS + 1)
    stored = await client.get_task(task_id)

    assert stored.status.state == TaskState.TASK_STATE_CANCELED
    assert slow_text(float(SHORT_SECONDS)) not in stored_texts(stored)

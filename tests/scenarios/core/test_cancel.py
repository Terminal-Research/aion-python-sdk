"""Cancelling a task that is still working through the public A2A endpoint.

A ``tasks/cancel`` request is the only way an external caller can stop work
it no longer wants.  The guarantee: once the server has accepted the cancel,
the task reaches CANCELED (not COMPLETED, not stuck in WORKING forever), and
a later ``tasks/get`` agrees.

The ``slow`` command emits a WORKING status immediately, then sleeps.  The
cancel arrives during the sleep, while the task is genuinely occupied.
"""

from __future__ import annotations

import pytest
from a2a.types import TaskState

from tests.scenarios.harness import ScenarioClient, final_task
from tests.scenarios.harness.recorder import Ev, _payload_of, to_event

pytestmark = [pytest.mark.terminal_states]


async def _send_and_cancel(client: ScenarioClient, slow_seconds: int = 30) -> tuple[str, list[Ev]]:
    """Start a slow task, cancel it once it reaches WORKING with text, collect events."""
    events: list[Ev] = []
    task_id: str | None = None

    stream = client.stream(f"slow {slow_seconds}")
    async for response in stream:
        ev = to_event(_payload_of(response))
        events.append(ev)
        if ev.task_id and task_id is None:
            task_id = ev.task_id
        if ev.state == "WORKING" and ev.text and task_id:
            await client.cancel(task_id)
            break

    assert task_id is not None, "stream did not carry a task id before WORKING"

    async for response in stream:
        events.append(to_event(_payload_of(response)))

    return task_id, events


@pytest.mark.command("slow")
async def test_cancel_stops_a_working_task(client: ScenarioClient) -> None:
    """A cancel request on a WORKING task lands it in CANCELED."""
    task_id, events = await _send_and_cancel(client)
    closing = final_task(events)
    assert closing.state == "CANCELED"


@pytest.mark.command("slow")
async def test_cancelled_task_is_stored_as_cancelled(client: ScenarioClient) -> None:
    """tasks/get after the cancel also says CANCELED, not the pre-cancel state."""
    task_id, events = await _send_and_cancel(client)

    stored = await client.get_task(task_id)
    assert stored.status.state == TaskState.TASK_STATE_CANCELED

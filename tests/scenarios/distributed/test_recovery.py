"""One server dies; what becomes of the tasks it was running.

The persistence suite kills a server and starts another on the same port,
which cannot tell recovery from replacement: the successor is the same
process by every name a test could read. Here nothing takes the dead
server's place, and the other server - already running, never the one
executing those tasks - is the one asked about them.

As in a2a-sdk's cluster mode, nothing detects a dead instance. Its tasks keep
the active state they were left in, with everything they had recorded, and
nothing settles them on its own. What the surviving server can do is what
any server can: cancel them, by writing ``CANCELED`` over their stored
version, which needs no execution to reach.
"""

from __future__ import annotations

import asyncio

import pytest
from a2a.types import TaskState

from tests.scenarios.harness import (
    ScenarioClient,
    ServeProcess,
    run_until_working,
    stored_texts,
)

pytestmark = [pytest.mark.distributed]

SLOW_SECONDS = 300
"""Far longer than the whole scenario, so the agent never ends a task itself."""

SETTLED_REASON_KEY = "aion:settledReason"
"""Task metadata saying the server, not the agent, decided the final state."""

ACTIVE_STATES = (TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_WORKING)

QUIET_SECONDS = 3
"""Spent watching nothing happen, after the cancel."""


@pytest.mark.command("slow")
async def test_a_dead_servers_task_stays_active_until_the_survivor_cancels_it(
    fresh_servers: tuple[ServeProcess, ServeProcess],
    fresh_clients: tuple[ScenarioClient, ScenarioClient],
) -> None:
    """The whole sequence, in the order a deployment would live it."""
    owner_server, _ = fresh_servers
    owner, survivor = fresh_clients

    # (1) A task observably occupied by the server that is about to die.
    task_id, _ = await run_until_working(owner, f"slow {SLOW_SECONDS}")
    history_before = stored_texts(await survivor.get_task(task_id))

    # (2) The owner is taken away with no chance to settle anything.
    owner_server.kill()

    # (3) The surviving server presents the task as it was left: nothing
    # settled it, and nothing will.
    left = await survivor.get_task(task_id)
    assert left.status.state in ACTIVE_STATES
    assert SETTLED_REASON_KEY not in left.metadata
    assert stored_texts(left) == history_before

    # (4) A cancel through the survivor closes it at once.
    answered = await survivor.cancel(task_id)
    assert answered.status.state == TaskState.TASK_STATE_CANCELED

    # (5) And that is the end of it: the task stays cancelled, history intact.
    await asyncio.sleep(QUIET_SECONDS)
    stored = await survivor.get_task(task_id)
    assert stored.status.state == TaskState.TASK_STATE_CANCELED
    for text in history_before:
        assert text in stored_texts(stored), f"what the task had already recorded is gone: {text!r}"

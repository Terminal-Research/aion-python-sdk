"""One server dies; the one that was already running closes what it left.

The persistence suite kills a server and starts another on the same port,
which cannot tell recovery from replacement: the successor is the same
process by every name a test could read. Here nothing takes the dead
server's place. Whatever becomes of its tasks is the work of the other
server, which was already running and is not the owner of anything it
settles - which is the guarantee a deployment actually depends on when a pod
disappears.

Two tasks and one crash, because the settlement has two branches and one
mechanism decides both: a task nobody asked about fails as
``lease_expired``, and a task somebody asked to cancel while its owner was
already gone is closed as ``cancel_requested``. Asking them separately would
mean waiting out a production lease twice for one branch of one ``if``.

They are not promised to arrive together. The two leases are taken moments
apart and expire moments apart, so a reconcile tick can fall between them
and settle one task on one pass and the other on the next. What the scenario
holds is the part that is actually guaranteed: one reconciliation mechanism
on the surviving server, one shared lease window to wait out, and both tasks
settled inside it - so the two are awaited concurrently under a single
deadline rather than one after the other with a budget each.

Nothing here is a race in the sense that matters. The cancel is recorded when
the owner is already dead, so there is no ordering between the kill and the
cancel to get wrong.
"""

from __future__ import annotations

import asyncio

import pytest
from a2a.types import Task, TaskState

from tests.scenarios.harness import (
    ScenarioClient,
    ServeProcess,
    eventually,
    run_until_working,
    stored_texts,
)
from tests.scenarios.harness.pg import claim_held, claim_released

pytestmark = [pytest.mark.distributed]

SLOW_SECONDS = 300
"""Far longer than the whole scenario, so the agent never ends a task itself."""

SETTLED_REASON_KEY = "aion:settledReason"
"""Task metadata saying the server, not the agent, decided the final state."""

ACTIVE_STATES = (TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_WORKING)

LEASE_TIMEOUT_SECONDS = 150
"""Room for the leases to expire and for the reaper to act on them.

``aion.server.tasks.ownership.config`` gives a lease 60 seconds and
reconciles every 30, and neither is settable from the outside, so this is the
worst case plus margin rather than a number chosen to be comfortable. It is
one deadline for both tasks, not one each: the two are awaited concurrently,
so a tick landing between their two expiries costs the scenario nothing.
"""

QUIET_SECONDS = 35
"""One reconcile interval plus margin, spent watching nothing happen.

Not a second lease: the leases are long gone by the time this starts. It is
the only way to state "no second settlement and no late write" as something
observable - a pass of the reaper runs inside this window, and the two tasks
have to come out of it byte for byte as they went in.
"""


def settled_reason(task: Task) -> str | None:
    """Why the server supplied this task's state, or None if the agent did."""
    return task.metadata[SETTLED_REASON_KEY] if SETTLED_REASON_KEY in task.metadata else None


async def settled(client: ScenarioClient, task_id: str, *, timeout: float) -> Task:
    """The task once it has left its active state, however long that takes."""

    async def probe() -> Task | None:
        task = await client.get_task(task_id)
        return task if task.status.state not in ACTIVE_STATES else None

    return await eventually(
        probe, what=f"task {task_id} being settled", timeout=timeout, interval=1.0
    )


@pytest.mark.command("slow")
async def test_a_dead_owner_leaves_two_tasks_and_the_survivor_closes_both(
    fresh_servers: tuple[ServeProcess, ServeProcess],
    fresh_clients: tuple[ScenarioClient, ScenarioClient],
) -> None:
    """The whole sequence, in the order a deployment would live it."""
    owner_server, _ = fresh_servers
    owner, survivor = fresh_clients

    # (1) Two tasks, both observably occupied by the server that is about to die.
    abandoned, _ = await run_until_working(owner, f"slow {SLOW_SECONDS}")
    to_cancel, _ = await run_until_working(owner, f"slow {SLOW_SECONDS}")
    history_before = {
        task_id: stored_texts(await survivor.get_task(task_id))
        for task_id in (abandoned, to_cancel)
    }
    assert await claim_held(abandoned) and await claim_held(to_cancel)

    # (2) The owner is taken away with no chance to settle anything, and
    # nothing is started in its place.
    owner_server.kill()

    # (3) A cancel recorded while the lease is still alive but its owner is
    # not. Nobody is left to honour the signal, so the answer is the task as
    # it currently stands - not a settlement.
    answered = await survivor.cancel(to_cancel)
    assert answered.status.state in ACTIVE_STATES, (
        f"the cancel settled a task whose owner was gone: {answered}"
    )

    # (4) Until the lease expires, both tasks are still someone's, and the
    # surviving server does not pick either of them up.
    for task_id in (abandoned, to_cancel):
        current = await survivor.get_task(task_id)
        assert current.status.state in ACTIVE_STATES
        assert await claim_held(task_id), "the dead owner's claim was released early"

    # (5) One wait for both, and the reaper on the survivor closes each for
    # its own reason. Awaited concurrently, which is what makes the budget
    # below one deadline for the pair rather than one apiece: the two leases
    # expire moments apart, a reconcile tick may fall between them, and
    # waiting for one and then the other would allow twice the time while
    # reading as a promise that a single pass settles both.
    failed, cancelled = await asyncio.gather(
        settled(survivor, abandoned, timeout=LEASE_TIMEOUT_SECONDS),
        settled(survivor, to_cancel, timeout=LEASE_TIMEOUT_SECONDS),
    )

    assert failed.status.state == TaskState.TASK_STATE_FAILED
    assert settled_reason(failed) == "lease_expired"
    assert cancelled.status.state == TaskState.TASK_STATE_CANCELED
    assert settled_reason(cancelled) == "cancel_requested"

    # (6) Both are readable through the survivor's public endpoint, history
    # intact, with their claims gone.
    for task_id, expected in history_before.items():
        replayed = await survivor.subscribe(task_id)
        assert [event.kind for event in replayed] == ["task"], (
            f"resubscribing to a settled task delivered more than it: {replayed}"
        )
        assert replayed[0].task_id == task_id
        kept = stored_texts(replayed[0].raw)
        for text in expected:
            assert text in kept, f"what the task had already recorded is gone: {text!r}"
        await claim_released(task_id)

    # (7) And that is the end of it: another pass of the reaper goes by and
    # neither task changes.
    await asyncio.sleep(QUIET_SECONDS)

    assert await survivor.get_task(abandoned) == failed, "the settled task was written again"
    assert await survivor.get_task(to_cancel) == cancelled, "the settled task was written again"

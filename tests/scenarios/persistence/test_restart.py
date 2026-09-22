"""What a server restart does to the tasks the previous process held.

The server writes tasks to PostgreSQL when it has a ``POSTGRES_URL``, so a
new OS process on the same port, the same config and the same database finds
them. Three things have to be true of what it finds.

A task that had already finished is untouched: the whole record - identity,
status, history, artifacts and metadata - comes back equal to what went in.
The restart is not an event in its life.

A task that was still running cannot be picked back up - the execution went
with the process - so the server settles it, and how it settles says which
restart happened. An orderly shutdown cancels what it is running and settles
those tasks itself, as ``FAILED`` with
``aion:settledReason=server_shutdown``. A process that was killed outright
settles nothing and leaves its task holding a lease it will never renew
again; the ownership reconciler reclaims it once the lease expires, as
``FAILED`` with ``lease_expired``. That second path is the slow one by
construction - the lease is what tells a dead owner from a busy one - and it
is the only guarantee a crash carries. Either way what the task had already
recorded stays recorded.

Which restart a task is started for is not timed: it is restarted on the
WORKING status the agent sends before it sleeps, the first moment the task is
observably occupied. Only the wait for the lease is a duration, because a
lease is one.
"""

from __future__ import annotations

import contextlib

import pytest
from a2a.types import TaskState

from tests.scenarios.commands import echo_text, step_text
from tests.scenarios.harness import (
    ScenarioClient,
    ServeProcess,
    eventually,
    final_task,
    reply_texts,
    stored_texts,
)
from tests.scenarios.harness.recorder import Ev, _payload_of, to_event

pytestmark = [pytest.mark.persistence]

SETTLED_REASON_KEY = "aion:settledReason"
"""Task metadata saying the server, not the agent, decided the final state."""

ACTIVE_STATES = (TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_WORKING)
"""What a task still being executed looks like in the store."""

SLOW_SECONDS = 120
"""Longer than any settlement here waits, so the agent never ends the task itself."""

LEASE_TIMEOUT_SECONDS = 150
"""Room for the lease to expire and for a reconcile pass to act on it.

``aion.server.tasks.ownership.config`` gives a lease 60 seconds and reconciles
every 30, and neither is settable from the outside, so this is the worst case
plus margin rather than a number chosen to be comfortable.
"""


async def reconnect(server: ServeProcess) -> ScenarioClient:
    """A client on the server that is running now, whichever process that is."""
    return await ScenarioClient.connect(server.base_url, server.variant.agent_id)


async def run_until_working(client: ScenarioClient, text: str) -> tuple[str, list[Ev]]:
    """Start a task and return as soon as it is observably working.

    The stream is closed on the way out and the task is left running: a
    subscriber going away is not an outcome, so the execution carries on
    until the restart takes it.
    """
    events: list[Ev] = []
    task_id: str | None = None

    stream = client.stream(text)
    try:
        async for response in stream:
            event = to_event(_payload_of(response))
            events.append(event)
            if event.task_id and task_id is None:
                task_id = event.task_id
            if event.state == "WORKING" and event.text and task_id:
                return task_id, events
    finally:
        with contextlib.suppress(Exception):
            await stream.aclose()

    raise AssertionError(f"the task never reached WORKING with a reply: {events}")


def settled_reason(task) -> str | None:
    """Why the server supplied this task's state, or None if the agent did."""
    metadata = task.metadata
    if SETTLED_REASON_KEY not in metadata:
        return None
    return metadata[SETTLED_REASON_KEY]


async def settled(client: ScenarioClient, task_id: str, *, timeout: float):
    """The task once it has left its active state, however long that takes."""

    async def probe():
        task = await client.get_task(task_id)
        return task if task.status.state not in ACTIVE_STATES else None

    return await eventually(
        probe, what=f"task {task_id} being settled", timeout=timeout, interval=1.0
    )


# --------------------------------------------------------------------------
# A task that had finished
# --------------------------------------------------------------------------

@pytest.mark.command("echo")
async def test_a_completed_task_is_unchanged_by_a_restart(
    pg_server: ServeProcess,
    pg_client: ScenarioClient,
) -> None:
    """The task read back after a restart is the task that was read before it."""
    events = await pg_client.send("echo survive-restart")
    closing = final_task(events)
    assert closing.state == "COMPLETED"
    assert echo_text("survive-restart") in reply_texts(events)

    before = await pg_client.get_task(closing.task_id)

    pg_server.restart()
    async with await reconnect(pg_server) as fresh_client:
        after = await fresh_client.get_task(closing.task_id)

    # Whole-message equality rather than a list of fields: the claim is that
    # nothing at all changed, and naming fields would only test the ones
    # someone thought to name. The state is asserted separately because
    # equality alone would be satisfied by two identically wrong tasks.
    assert after.status.state == TaskState.TASK_STATE_COMPLETED
    assert after == before, (
        f"the restart changed the stored task.\nbefore:\n{before}\nafter:\n{after}"
    )


@pytest.mark.command("steps")
async def test_a_multi_step_history_survives_a_restart(
    pg_server: ServeProcess,
    pg_client: ScenarioClient,
) -> None:
    """Every reply of a multi-step turn is still there, in order, afterwards."""
    events = await pg_client.send("steps 3")
    task_id = final_task(events).task_id

    before = await pg_client.get_task(task_id)
    assert [step_text(index, 3) for index in (1, 2, 3)] == [
        text for text in stored_texts(before) if text.startswith("step ")
    ]

    pg_server.restart()
    async with await reconnect(pg_server) as fresh_client:
        after = await fresh_client.get_task(task_id)

    assert stored_texts(after) == stored_texts(before)


# --------------------------------------------------------------------------
# A task that was still running
# --------------------------------------------------------------------------

@pytest.mark.command("slow")
async def test_a_running_task_is_settled_by_an_orderly_shutdown(
    pg_server: ServeProcess,
    pg_client: ScenarioClient,
) -> None:
    """A shutdown cancels what it is running and says so on the task it leaves."""
    task_id, _ = await run_until_working(pg_client, f"slow {SLOW_SECONDS}")

    pg_server.restart()
    async with await reconnect(pg_server) as fresh_client:
        settled = await fresh_client.get_task(task_id)

    assert settled.status.state == TaskState.TASK_STATE_FAILED
    assert settled_reason(settled) == "server_shutdown"


@pytest.mark.command("slow")
async def test_a_running_task_is_settled_after_a_crash_when_its_lease_expires(
    pg_server: ServeProcess,
    pg_client: ScenarioClient,
) -> None:
    """A killed owner settles nothing, so the lease it stopped renewing does.

    What the task had already recorded is read here too, from the settled
    task rather than from the active one: the question is what survives the
    whole crash, settlement included, and waiting out a production lease twice
    to ask it separately would buy nothing.
    """
    text = f"slow {SLOW_SECONDS}"
    task_id, streamed = await run_until_working(pg_client, text)
    delivered = [event.text for event in streamed if event.kind == "status" and event.text]

    pg_server.crash_restart()
    async with await reconnect(pg_server) as fresh_client:
        task = await settled(fresh_client, task_id, timeout=LEASE_TIMEOUT_SECONDS)

    assert task.id == task_id
    assert task.status.state == TaskState.TASK_STATE_FAILED
    assert settled_reason(task) == "lease_expired"

    history = stored_texts(task)
    assert text in history, "the message that opened the task is gone"
    for reply in delivered:
        assert reply in history, f"a reply the client had already seen is gone: {reply!r}"

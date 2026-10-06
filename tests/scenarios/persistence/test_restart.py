"""What a server restart does to the tasks the previous process held.

The server writes tasks to PostgreSQL when it has a ``POSTGRES_URL``, so a
new OS process on the same port, the same config and the same database finds
them. Three things have to be true of what it finds.

A task that had already finished is untouched: the whole record - identity,
status, history, artifacts and metadata - comes back equal to what went in.
The restart is not an event in its life.

A task that was still running cannot be picked back up - the execution went
with the process - and what happens to it says which restart happened. An
orderly shutdown cancels what it is running and settles those tasks itself, as
``FAILED`` with ``aion:settledReason=server_shutdown``. A process that was
killed outright settles nothing: as in a2a-sdk's cluster mode, nothing
detects a dead instance, so the task keeps the active state it had until
someone cancels it - which any server can, by writing ``CANCELED`` over its
stored version. Either way what the task had already recorded stays recorded.

Which restart a task is started for is not timed: it is restarted on the
WORKING status the agent sends before it sleeps, the first moment the task is
observably occupied.
"""

from __future__ import annotations

import pytest
from a2a.types import TaskState

from tests.scenarios.commands import echo_text, step_text
from tests.scenarios.harness import (
    ScenarioClient,
    ServeProcess,
    final_task,
    reply_texts,
    run_until_working,
    stored_texts,
)

pytestmark = [pytest.mark.persistence]

SETTLED_REASON_KEY = "aion:settledReason"
"""Task metadata saying the server, not the agent, decided the final state."""

ACTIVE_STATES = (TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_WORKING)
"""What a task still being executed looks like in the store."""

SLOW_SECONDS = 120
"""Longer than any settlement here waits, so the agent never ends the task itself."""

async def reconnect(server: ServeProcess) -> ScenarioClient:
    """A client on the server that is running now, whichever process that is."""
    return await ScenarioClient.connect(server.base_url, server.variant.agent_id)


def settled_reason(task) -> str | None:
    """Why the server supplied this task's state, or None if the agent did."""
    metadata = task.metadata
    if SETTLED_REASON_KEY not in metadata:
        return None
    return metadata[SETTLED_REASON_KEY]


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
async def test_a_crashed_servers_task_stays_active_until_it_is_cancelled(
    pg_server: ServeProcess,
    pg_client: ScenarioClient,
) -> None:
    """A killed process settles nothing, and its successor does not either.

    Starting is not evidence that a previous instance died, so the task is
    presented exactly as it was left - active, with everything it recorded.
    A cancel on the new process closes it: a2a-sdk's remote cancellation,
    ``CANCELED`` written over the stored version, needs no execution to reach.
    """
    text = f"slow {SLOW_SECONDS}"
    task_id, streamed = await run_until_working(pg_client, text)
    delivered = [event.text for event in streamed if event.kind == "status" and event.text]

    pg_server.crash_restart()
    async with await reconnect(pg_server) as fresh_client:
        left = await fresh_client.get_task(task_id)
        cancelled = await fresh_client.cancel(task_id)
        stored = await fresh_client.get_task(task_id)

    assert left.status.state in ACTIVE_STATES
    assert settled_reason(left) is None
    history = stored_texts(left)
    assert text in history, "the message that opened the task is gone"
    for reply in delivered:
        assert reply in history, f"a reply the client had already seen is gone: {reply!r}"

    assert cancelled.status.state == TaskState.TASK_STATE_CANCELED
    assert stored.status.state == TaskState.TASK_STATE_CANCELED
    assert stored_texts(stored) == history

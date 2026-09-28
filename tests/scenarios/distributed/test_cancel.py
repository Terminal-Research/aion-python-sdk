"""Cancelling a task through a server that is not the one running it.

``tasks/cancel`` may arrive anywhere. Whichever process receives it, the
execution it has to stop belongs to another one, and no process can run
another's teardown: the receiving server marks the claim and the owner acts
on it locally, over ``TASK_EVENT_CHANNEL``. That path has never run between
two operating-system processes - the integration tests listen on one database
from a single interpreter, and the scenario cancel never left one server - so
it is what these scenarios drive.

The owner is alive throughout. A cancel racing the death of its owner is a
different guarantee and lives in ``test_recovery.py``; nothing here waits for
a lease.

Two cancels are two different questions, and the scenarios keep them apart.
A second one sent after the task is already settled must be refused as not
cancelable, because a successful cancellation is itself terminal and would
otherwise be indistinguishable from one. Two genuinely concurrent ones must
agree on the *outcome* - the task ends cancelled once, and nothing answers
late - but not necessarily on the *reply* each caller gets: with only two
servers one of the two cancels reaches the owner itself, and an owner that
finishes settling before the other request is served refuses that one as not
cancelable. Both answers are correct, and which pair arrives is a race, so
the scenario asserts the outcome and allows either reply.

An owner that stays alive but never honors the cancel is not driven here.
The reaper closes such a task as CANCELED with the ``cancel_timeout`` reason
once ``CANCEL_GRACE_SECONDS`` (120 s) has passed, and that grace must stay
longer than the evolution rescue drain, so a scenario would spend more than
two minutes per framework waiting for it. The chain is checked at the
integration level instead, by
``test_an_owner_that_ignores_a_cancellation_is_forced_closed`` in
``tests/integration/server/tasks/test_task_ownership_e2e_postgres.py``.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest
from a2a.types import TaskState

from tests.scenarios.commands import slow_text
from tests.scenarios.harness import ScenarioClient, run_until_working, stored_texts
from tests.scenarios.harness.pg import claim_released
from tests.scenarios.harness.recorder import Ev, _payload_of, to_event

pytestmark = [pytest.mark.distributed]

SLOW_SECONDS = 20
"""Long enough that a cancel always lands mid-sleep."""

NOT_CANCELABLE_CODE = -32002
"""What A2A answers a cancel of a task that has already finished."""

SETTLE_TIMEOUT_SECONDS = 30
"""A cross-process cancel that has not landed by now is not merely slow."""


def _cancel_answer(response: dict) -> str:
    """Name what a ``CancelTask`` response is, for the concurrent case.

    Only the two answers a concurrent cancel may correctly receive are named;
    anything else is returned verbatim so the assertion that rejects it can
    print what actually arrived.
    """
    error = response.get("error")
    if error is not None:
        if error.get("code") == NOT_CANCELABLE_CODE:
            return "not cancelable"
        return f"error {error.get('code')}"
    state = (response.get("result") or {}).get("status", {}).get("state")
    if state == "TASK_STATE_CANCELED":
        return "cancelled"
    return f"state {state}"


async def cancelled_everywhere(*clients: ScenarioClient, task_id: str) -> None:
    """Assert every server reports the same cancelled task."""
    for client in clients:
        stored = await client.get_task(task_id)
        assert stored.status.state == TaskState.TASK_STATE_CANCELED, (
            f"{client.base_url} reports {stored.status.state} for task {task_id}"
        )


@pytest.mark.command("slow")
async def test_a_cancel_on_the_other_server_reaches_the_owner(
    clients: tuple[ScenarioClient, ScenarioClient],
) -> None:
    """The owner's own stream closes as CANCELED, on a cancel it never received.

    The stream the owner opened is kept open here rather than closed the way
    ``run_until_working`` leaves it: what arrives on it is the delivery
    itself, across the process boundary, and not an outcome read back
    afterwards.
    """
    first, second = clients
    events: list[Ev] = []
    task_id: str | None = None

    stream = first.stream(f"slow {SLOW_SECONDS}")
    try:
        async for response in stream:
            event = to_event(_payload_of(response))
            events.append(event)
            if event.task_id and task_id is None:
                task_id = event.task_id
            if event.state == "WORKING" and event.text and task_id:
                break

        assert task_id is not None
        answered = await second.cancel(task_id)
        assert answered.status.state == TaskState.TASK_STATE_CANCELED

        async for response in stream:
            events.append(to_event(_payload_of(response)))
    finally:
        with contextlib.suppress(Exception):
            await stream.aclose()

    closing = events[-1]
    assert closing.kind == "task"
    assert closing.state == "CANCELED", f"the owner's stream did not end cancelled: {events}"
    await cancelled_everywhere(first, second, task_id=task_id)


@pytest.mark.command("slow")
async def test_the_cancelled_work_produces_no_late_answer(
    clients: tuple[ScenarioClient, ScenarioClient],
) -> None:
    """The reply the agent owed never lands, on either server, and the claim goes."""
    first, second = clients

    task_id, _ = await run_until_working(first, f"slow {SLOW_SECONDS}")
    await second.cancel(task_id)
    await claim_released(task_id, timeout=SETTLE_TIMEOUT_SECONDS)

    stored = await first.get_task(task_id)
    assert stored.status.state == TaskState.TASK_STATE_CANCELED
    assert slow_text(float(SLOW_SECONDS)) not in stored_texts(stored)
    await cancelled_everywhere(first, second, task_id=task_id)


@pytest.mark.command("slow")
async def test_two_concurrent_cancels_agree(
    clients: tuple[ScenarioClient, ScenarioClient],
) -> None:
    """Concurrent cancels reach one outcome, and no caller is told otherwise.

    With two servers, one of these two requests goes to the owner, so the
    pair of replies is genuinely racy: either both are answered with the
    cancelled task, or the owner settles first and the other request is
    refused as not cancelable. Demanding two CANCELED replies would fail on
    a legitimate ordering. What is not racy is the outcome - exactly one
    cancellation happens, it holds, and neither caller is answered with a
    state the task never reached - so that is what this asserts.

    Sent as raw JSON-RPC because a refusal is one of the two correct answers
    here and the typed client renders it as an exception, losing the code
    that says which refusal it was.
    """
    first, second = clients

    task_id, _ = await run_until_working(first, f"slow {SLOW_SECONDS}")
    here, there = await asyncio.gather(
        first.rpc("CancelTask", {"id": task_id}),
        second.rpc("CancelTask", {"id": task_id}),
    )

    answers = [_cancel_answer(response) for response in (here, there)]
    assert "cancelled" in answers, (
        f"neither cancel was answered with the cancelled task: {here} {there}"
    )
    for answer in answers:
        assert answer in ("cancelled", "not cancelable"), (
            f"a concurrent cancel was answered with neither: {here} {there}"
        )

    await claim_released(task_id, timeout=SETTLE_TIMEOUT_SECONDS)
    await cancelled_everywhere(first, second, task_id=task_id)
    stored = await first.get_task(task_id)
    assert slow_text(float(SLOW_SECONDS)) not in stored_texts(stored), (
        "the cancelled work answered anyway"
    )


@pytest.mark.command("slow")
async def test_cancelling_a_settled_task_again_is_refused(
    clients: tuple[ScenarioClient, ScenarioClient],
) -> None:
    """A repeat is not a second cancellation but an error, and changes nothing.

    Sequential, not concurrent: the first cancel has already settled the task
    before the second is sent, which is the case the A2A error exists for.
    """
    first, second = clients

    task_id, _ = await run_until_working(first, f"slow {SLOW_SECONDS}")
    assert (await second.cancel(task_id)).status.state == TaskState.TASK_STATE_CANCELED

    again = await second.rpc("CancelTask", {"id": task_id})

    assert "error" in again, f"a settled task accepted a second cancel: {again}"
    assert again["error"]["code"] == NOT_CANCELABLE_CODE, (
        f"the repeat was refused, but not as a task that cannot be cancelled: {again}"
    )
    await cancelled_everywhere(first, second, task_id=task_id)


@pytest.mark.command("slow")
async def test_a_cancelled_task_never_reaches_another_terminal_state(
    clients: tuple[ScenarioClient, ScenarioClient],
) -> None:
    """The outcome holds: nothing overwrites it once the work has stopped.

    The one place a deadline is the instrument rather than a substitute for
    one - an absence has no event to wait for. The agent was asked to answer
    after a delay this waits past, so a run that had continued behind the
    cancel would show up here.
    """
    first, second = clients
    seconds = 3

    task_id, _ = await run_until_working(first, f"slow {seconds}")
    await second.cancel(task_id)
    await claim_released(task_id, timeout=SETTLE_TIMEOUT_SECONDS)

    await asyncio.sleep(seconds + 1)

    for client in (first, second):
        stored = await client.get_task(task_id)
        assert stored.status.state == TaskState.TASK_STATE_CANCELED
        assert slow_text(float(seconds)) not in stored_texts(stored)

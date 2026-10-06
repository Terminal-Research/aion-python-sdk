"""Cancelling a task through a server that is not the one running it.

``tasks/cancel`` may arrive anywhere. The server that receives it cancels a
task it is running through its executor; any other task gets a2a-sdk's remote
cancellation: ``CANCELED`` is written over the task's stored version, and the
cancel is answered at once. The server executing the task finds out at its
next write, which no longer matches the version, rereads the task, sees it
terminal and stops the execution. ``AgentExecutor.cancel`` is not called
there.

So the owner's own stream closes as CANCELED only once its agent writes
again, and the reply the cancelled work owed never lands. That is what these
scenarios drive.

Two cancels are two different questions, and the scenarios keep them apart.
A second one sent after the task is already settled must be refused as not
cancelable, because a successful cancellation is itself terminal and would
otherwise be indistinguishable from one. Two genuinely concurrent ones must
agree on the *outcome* - the task ends cancelled once, and nothing answers
late - but not necessarily on the *reply* each caller gets: one of them may
find the task already cancelled by the other. Both answers are correct, and
which pair arrives is a race, so the scenario asserts the outcome and allows
either reply.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest
from a2a.types import TaskState

from tests.scenarios.commands import slow_text
from tests.scenarios.harness import ScenarioClient, run_until_working, stored_texts
from tests.scenarios.harness.recorder import Ev, _payload_of, to_event

pytestmark = [pytest.mark.distributed]

SLOW_SECONDS = 6
"""Long enough that a cancel always lands mid-sleep, short enough to wait out.

The owner learns of a remote cancel at its next write, which for ``slow`` is
the reply after the sleep, so a scenario that watches the owner stop waits
this long.
"""

NOT_CANCELABLE_CODE = -32002
"""What A2A answers a cancel of a task that has already finished."""


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
    afterwards. It arrives when the owner's agent writes again and finds the
    task cancelled.
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
    """The reply the agent owed never lands, on either server."""
    first, second = clients

    task_id, _ = await run_until_working(first, f"slow {SLOW_SECONDS}")
    await second.cancel(task_id)
    await asyncio.sleep(SLOW_SECONDS + 1)

    stored = await first.get_task(task_id)
    assert stored.status.state == TaskState.TASK_STATE_CANCELED
    assert slow_text(float(SLOW_SECONDS)) not in stored_texts(stored)
    await cancelled_everywhere(first, second, task_id=task_id)


@pytest.mark.command("slow")
async def test_two_concurrent_cancels_agree(
    clients: tuple[ScenarioClient, ScenarioClient],
) -> None:
    """Concurrent cancels reach one outcome, and no caller is told otherwise.

    With two servers, one of these two requests goes to the owner, which
    cancels through its executor, and the other writes ``CANCELED`` over the
    stored version. The pair of replies is genuinely racy: either both are
    answered with the cancelled task, or one finds the task already settled
    and is refused as not cancelable. Demanding two CANCELED replies would
    fail on a legitimate ordering. What is not racy is the outcome - exactly one
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

    await asyncio.sleep(SLOW_SECONDS + 1)
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

    await asyncio.sleep(seconds + 1)

    for client in (first, second):
        stored = await client.get_task(task_id)
        assert stored.status.state == TaskState.TASK_STATE_CANCELED
        assert slow_text(float(seconds)) not in stored_texts(stored)

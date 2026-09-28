"""An agent that crashes must leave a task that says so.

A failure inside an agent is the one path where the server has to speak for
it. Two things can go wrong and neither shows up in a unit test of the
executor: the caller is left holding a stream that simply stops, or the task
stays WORKING in the store forever, so every later `tasks/get` reports work
still in progress that nothing is doing.

Both modes of `fail` are here, and the difference between them is what the
caller already has when the crash happens.
"""

from __future__ import annotations

import pytest
from a2a.types import TaskState

from tests.scenarios.commands import FAIL_MESSAGE, FAIL_REPLY_TEXT, echo_text
from tests.scenarios.harness import (
    ScenarioClient,
    assert_shape,
    final_task,
    reply_texts,
    status,
    task,
)

pytestmark = [pytest.mark.errors, pytest.mark.terminal_states]


@pytest.mark.command("fail")
async def test_a_crash_before_any_answer_closes_the_task_as_failed(
    client: ScenarioClient,
) -> None:
    """The stream ends in a terminal task, not in silence.

    Nothing was produced by the turn, so this is the whole of what the caller
    receives - and a client waiting for a terminal event would otherwise wait
    for one that never arrives.
    """
    events = await client.send("fail exception")

    assert_shape(events, [
        task("SUBMITTED"),
        status("WORKING", text=""),
        task("FAILED"),
    ])
    assert final_task(events).final is True


@pytest.mark.command("fail")
async def test_a_crash_after_a_reply_keeps_the_reply(client: ScenarioClient) -> None:
    """What was already delivered stays delivered, and the task still fails.

    The agent answered and then crashed. Dropping the answer would contradict
    what the caller already received; hiding the failure would leave them
    believing the turn finished.
    """
    events = await client.send("fail after-reply")

    assert FAIL_REPLY_TEXT in reply_texts(events)
    assert final_task(events).state == "FAILED"


@pytest.mark.command("fail")
async def test_the_failure_is_in_the_stored_task_not_only_on_the_wire(
    client: ScenarioClient,
) -> None:
    """A later `tasks/get` reports the failure too.

    This is the half a unit test cannot see: the JSON-RPC error reaches the
    caller of this one request, while the stored task is what every other
    reader of it sees.
    """
    events = await client.send("fail exception")
    task_id = final_task(events).task_id

    stored = await client.get_task(task_id)

    assert stored.status.state == TaskState.TASK_STATE_FAILED


@pytest.mark.command("fail")
async def test_the_failed_task_does_not_carry_the_exception(client: ScenarioClient) -> None:
    """The reason is logged, not published.

    An exception's text is internal detail - paths, library names, whatever
    the agent happened to interpolate. The task says it failed and no more.
    """
    events = await client.send("fail exception")
    closing = final_task(events)

    stored = await client.get_task(closing.task_id)

    assert FAIL_MESSAGE not in closing.text
    assert not any(FAIL_MESSAGE in part.text for message in stored.history for part in message.parts)


@pytest.mark.command("fail")
async def test_a_failing_turn_does_not_take_the_agent_with_it(client: ScenarioClient) -> None:
    """The next request is answered normally.

    A crash inside one turn is an ordinary event; a crash that leaves the
    server unable to serve the next one is an outage.
    """
    await client.send("fail exception")

    events = await client.send("echo still here")

    assert echo_text("still here") in reply_texts(events)
    assert final_task(events).state == "COMPLETED"

"""What ``tasks/resubscribe`` gives a client that comes back to a task.

A caller that loses its stream has one way back in, and what it gets depends
on what the task is doing. A task still running hands over its remaining
events and its outcome. A task that already has an outcome has no execution
to join, so the server replays the stored ``Task`` and closes the stream -
which is the answer to the most ordinary reconnect there is, reading the
result of a finished turn, and the reason a reaper settles a task rather than
deleting it.

A task waiting for input is the third case, and it is the one where the
answer depends on the deployment rather than on the protocol. These scenarios
run on the default in-memory deployment, where ownership is not enforced and
one process both holds the paused task and will carry its next turn: a
subscriber attaches to that execution and stays attached, so the resume
another client sends reaches it. The PostgreSQL contract is the other one and
is written down where a database exists to show it -
``persistence/test_resubscribe.py``. Neither is the general rule; both are
current behaviour, and the difference follows from enforcement being a
property of the durable build.

Nothing here waits for a lease.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest
import pytest_asyncio
from a2a.types import TaskState

from tests.scenarios.commands import (
    FAIL_REPLY_TEXT,
    ask_answer_text,
    ask_question,
    echo_text,
    slow_text,
)
from tests.scenarios.harness import (
    Ev,
    ScenarioClient,
    ServeProcess,
    final_task,
    reply_texts,
    run_until_working,
    stored_texts,
)
from tests.scenarios.harness.recorder import _payload_of, to_event

pytestmark = [pytest.mark.events]

SLOW_SECONDS = 5
"""Long enough to resubscribe while it runs, short enough to then wait out."""

RESUME_ANSWER = "resubscribed answer"
RESUME_TIMEOUT_SECONDS = 60
"""A subscriber still waiting after this is not slow but stuck."""


@pytest_asyncio.fixture(loop_scope="session")
async def other_client(server: ServeProcess) -> AsyncIterator[ScenarioClient]:
    """A second client on the same agent: the one that is not subscribed."""
    client = await ScenarioClient.connect(server.base_url, server.variant.agent_id)
    try:
        yield client
    finally:
        await client.close()


async def next_event(stream: AsyncIterator) -> Ev:
    """The next event of a raw stream, projected."""
    return to_event(_payload_of(await anext(stream)))


async def drain(stream: AsyncIterator) -> list[Ev]:
    """Everything left on a raw stream, until the server closes it."""
    return [to_event(_payload_of(response)) async for response in stream]


# --------------------------------------------------------------------------
# A task that is still running
# --------------------------------------------------------------------------

@pytest.mark.command("slow")
async def test_a_running_task_hands_its_remaining_events_to_a_subscriber(
    client: ScenarioClient,
) -> None:
    """The reconnecting client sees the rest of the turn, outcome included."""
    task_id, opened = await run_until_working(client, f"slow {SLOW_SECONDS}")
    assert slow_text(float(SLOW_SECONDS)) not in reply_texts(opened), (
        "the task finished before the subscription, so nothing was resubscribed to"
    )

    resubscribed = await client.subscribe(task_id)

    closing = final_task(resubscribed)
    assert closing.task_id == task_id
    assert closing.state == "COMPLETED"
    assert slow_text(float(SLOW_SECONDS)) in reply_texts(resubscribed)


@pytest.mark.command("slow")
async def test_resubscribing_does_not_start_the_turn_again(
    client: ScenarioClient,
) -> None:
    """One execution, one task: the reply is said once, not once per subscriber."""
    task_id, opened = await run_until_working(client, f"slow {SLOW_SECONDS}")
    # The Task the stream opened with, which is the only one it carried: the
    # turn was left running, so its terminal Task has not arrived yet.
    context_id = final_task(opened).context_id

    await client.subscribe(task_id)

    stored = await client.get_task(task_id)
    answer = slow_text(float(SLOW_SECONDS))
    assert stored_texts(stored).count(answer) == 1
    assert [task.id for task in await client.tasks_in_context(context_id)] == [task_id]


# --------------------------------------------------------------------------
# A task that already has an outcome
# --------------------------------------------------------------------------

@pytest.mark.command("echo")
async def test_a_settled_task_is_replayed_once_and_the_stream_closes(
    client: ScenarioClient,
) -> None:
    """A finished turn answers a subscriber with the task it left behind."""
    task_id = final_task(await client.send("echo resubscribe-me")).task_id

    resubscribed = await client.subscribe(task_id)

    assert [event.kind for event in resubscribed] == ["task"]
    assert resubscribed[0].state == "COMPLETED"
    assert resubscribed[0].final
    assert echo_text("resubscribe-me") in resubscribed[0].text


@pytest.mark.command("echo")
async def test_the_replayed_task_is_the_stored_task(client: ScenarioClient) -> None:
    """Resubscribe and tasks/get answer with the same record, not two readings."""
    task_id = final_task(await client.send("echo same-as-get")).task_id

    replayed = (await client.subscribe(task_id))[0].raw
    fetched = await client.get_task(task_id)

    assert replayed == fetched, (
        f"resubscribe and tasks/get disagree.\nreplayed:\n{replayed}\nfetched:\n{fetched}"
    )


@pytest.mark.command("fail")
async def test_a_failed_task_is_replayed_as_failed(client: ScenarioClient) -> None:
    """The outcome a subscriber is given is the one the task has, not a fresh run."""
    task_id = final_task(await client.send("fail after-reply")).task_id

    resubscribed = await client.subscribe(task_id)

    assert [event.kind for event in resubscribed] == ["task"]
    assert resubscribed[0].state == "FAILED"
    assert FAIL_REPLY_TEXT in stored_texts(resubscribed[0].raw)


@pytest.mark.command("slow")
async def test_a_cancelled_task_is_replayed_as_cancelled(client: ScenarioClient) -> None:
    """A cancel is an outcome like any other, and replays like one."""
    task_id, _ = await run_until_working(client, f"slow {SLOW_SECONDS}")
    assert (await client.cancel(task_id)).status.state == TaskState.TASK_STATE_CANCELED

    resubscribed = await client.subscribe(task_id)

    assert [event.kind for event in resubscribed] == ["task"]
    assert resubscribed[0].state == "CANCELED"


# --------------------------------------------------------------------------
# A task waiting for input, on this deployment
# --------------------------------------------------------------------------

@pytest.mark.command("ask")
async def test_a_paused_task_keeps_a_subscriber_until_another_client_resumes_it(
    client: ScenarioClient,
    other_client: ScenarioClient,
) -> None:
    """The subscriber is given the pause, then the turn that ends it.

    On this deployment the paused execution is still here, so the
    subscription is a live attachment rather than a replay: the first event
    is the task as it stands, and the stream stays open until something
    continues the task. That something is a second client - the resume is
    sent while the subscription is held, which is the whole point.
    """
    opened = final_task(await client.send("ask"))
    assert opened.state == "INPUT_REQUIRED"
    assert ask_question() in opened.text

    stream = client.subscribe_stream(opened.task_id)
    attached = await next_event(stream)

    assert attached.kind == "task"
    assert attached.task_id == opened.task_id
    assert attached.state == "INPUT_REQUIRED"
    assert ask_question() in attached.text, "the subscriber was not told what was asked"

    subscriber = asyncio.create_task(drain(stream))
    resumed = await other_client.send(
        RESUME_ANSWER, task_id=opened.task_id, context_id=opened.context_id
    )
    delivered = await asyncio.wait_for(subscriber, timeout=RESUME_TIMEOUT_SECONDS)

    assert final_task(resumed).state == "COMPLETED"
    assert ask_answer_text(RESUME_ANSWER) in reply_texts(delivered), (
        f"the subscriber did not see the continuation: {delivered}"
    )
    closing = final_task(delivered)
    assert closing.task_id == opened.task_id
    assert closing.state == "COMPLETED"


@pytest.mark.command("ask")
async def test_resuming_under_a_subscriber_keeps_one_task_and_its_question(
    client: ScenarioClient,
    other_client: ScenarioClient,
) -> None:
    """No second task, and nothing the pause had recorded is lost."""
    opened = final_task(await client.send("ask"))

    stream = client.subscribe_stream(opened.task_id)
    await next_event(stream)
    subscriber = asyncio.create_task(drain(stream))
    await other_client.send(
        RESUME_ANSWER, task_id=opened.task_id, context_id=opened.context_id
    )
    await asyncio.wait_for(subscriber, timeout=RESUME_TIMEOUT_SECONDS)

    stored = await client.get_task(opened.task_id)
    texts = stored_texts(stored)

    assert stored.id == opened.task_id
    assert stored.status.state == TaskState.TASK_STATE_COMPLETED
    assert ask_question() in texts, "the question the task paused on is gone"
    assert RESUME_ANSWER in texts
    assert [task.id for task in await client.tasks_in_context(opened.context_id)] == [
        opened.task_id
    ]

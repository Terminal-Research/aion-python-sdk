"""The shape of a turn: which events arrive, in which order, under which ids.

This module is where the observed stream contract is written down. Every
other scenario builds on it, so a change here is a change in what the SDK
promises a client.
"""

from __future__ import annotations

import json

import pytest

from tests.scenarios.commands import IdsResponse, steps_texts
from tests.scenarios.harness import (
    SKIP,
    ScenarioClient,
    assert_shape,
    final_task,
    reply_texts,
    status,
    task,
)

pytestmark = pytest.mark.events


@pytest.mark.command("echo")
async def test_a_turn_opens_with_submitted_and_closes_with_the_task(client: ScenarioClient) -> None:
    """A plain turn is: Task SUBMITTED, WORKING, the reply, the terminal Task.

    The bare WORKING status is the transition into a running task; the reply
    is delivered as a second WORKING status carrying the message. The stream
    closes with the Task itself, which is what a caller acts on.
    """
    events = await client.send("echo shape")

    assert_shape(events, [
        task("SUBMITTED"),
        status("WORKING", text=""),
        status("WORKING", text="shape"),
        task("COMPLETED"),
    ])
    assert final_task(events).final is True


@pytest.mark.command("steps")
async def test_working_statuses_arrive_in_order(client: ScenarioClient) -> None:
    """`steps <n>` reports each step once, in order, before completing."""
    events = await client.send("steps 3")

    assert reply_texts(events) == list(steps_texts(3))
    assert_shape(events, [
        task("SUBMITTED"),
        status("WORKING", text=""),
        status("WORKING", text="step 1/3"),
        status("WORKING", text="step 2/3"),
        status("WORKING", text="step 3/3"),
        task("COMPLETED"),
    ])


@pytest.mark.command("ids")
async def test_the_agent_sees_the_ids_the_client_sees(client: ScenarioClient) -> None:
    """The task and context the agent reports are the ones on the wire."""
    events = await client.send("ids")

    closing = final_task(events)
    reported = IdsResponse.model_validate_json(reply_texts(events)[-1])

    assert reported.task_id == closing.task_id
    assert reported.context_id == closing.context_id


@pytest.mark.command("ids")
async def test_a_second_message_in_a_context_starts_a_new_task(client: ScenarioClient) -> None:
    """Reusing a context keeps it and starts a fresh task under it."""
    first = final_task(await client.send("ids"))
    second = final_task(await client.send("ids", context_id=first.context_id))

    assert second.context_id == first.context_id
    assert second.task_id != first.task_id


@pytest.mark.command("echo")
async def test_get_task_agrees_with_the_closing_event(client: ScenarioClient) -> None:
    """Reading the task back gives the state and the text the stream ended on."""
    events = await client.send("echo stored")
    closing = final_task(events)

    stored = await client.get_task(closing.task_id)

    assert stored.id == closing.task_id
    assert stored.context_id == closing.context_id
    assert stored.status.state == closing.raw.status.state
    assert "stored" in json.dumps(
        [part.text for message in stored.history for part in message.parts]
    )


@pytest.mark.command("echo")
async def test_the_stream_carries_nothing_after_the_terminal_task(client: ScenarioClient) -> None:
    """Nothing follows the closing Task, and the stream ends there."""
    events = await client.send("echo last")

    assert events[-1].kind == "task"
    assert_shape(events, [SKIP, task("COMPLETED")])

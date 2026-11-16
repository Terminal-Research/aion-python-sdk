"""The shape of a turn: which events arrive, in which order, under which ids.

This module is where the observed stream contract is written down. Every
other scenario builds on it, so a change here is a change in what the SDK
promises a client.
"""

from __future__ import annotations

import json

import pytest

from tests.scenarios.commands import (
    ARTIFACT_DATA_NAME,
    ARTIFACT_FILE_BYTES,
    ARTIFACT_FILE_NAME,
    ARTIFACT_URL_NAME,
    HistoryResponse,
    IdsResponse,
    artifacts_data_parts,
    artifacts_text,
    steps_texts,
)
from tests.scenarios.harness import (
    SKIP,
    ScenarioClient,
    answer,
    assert_shape,
    final_task,
    reply_texts,
    status,
    task,
)

pytestmark = pytest.mark.events


@pytest.mark.command("echo")
async def test_a_turn_opens_with_submitted_and_closes_with_the_task(client: ScenarioClient) -> None:
    """A plain turn is: Task SUBMITTED, the reply as WORKING, the reply as the answer, the terminal Task.

    The WORKING status carrying the reply is the transition into a running
    task; no status without content precedes it. A task that produced
    nothing but messages gets the last one as its ``aion:assistant-answer``
    right before it completes. The stream closes with the Task itself, which
    is what a caller acts on.
    """
    events = await client.send("echo shape")

    assert_shape(events, [
        task("SUBMITTED"),
        status("WORKING", text="shape"),
        answer(text="shape"),
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
        status("WORKING", text="step 1/3"),
        status("WORKING", text="step 2/3"),
        status("WORKING", text="step 3/3"),
        answer(text="step 3/3"),
        task("COMPLETED"),
    ])
    statuses = [event for event in events if event.kind in ("task", "status")]
    for event in statuses:
        assert event.raw.status.HasField("timestamp"), event
    completed_at = final_task(events).raw.status.timestamp.ToNanoseconds()
    for event in statuses:
        assert event.raw.status.timestamp.ToNanoseconds() <= completed_at, event


@pytest.mark.command("steps")
async def test_status_times_follow_the_turn(client: ScenarioClient) -> None:
    """Every status of a turn, the opening SUBMITTED included, is no earlier than the one before it."""
    events = await client.send("steps 2")

    times = [
        event.raw.status.timestamp.ToNanoseconds()
        for event in events
        if event.kind in ("task", "status")
    ]
    assert events[0].state == "SUBMITTED"
    assert len(times) >= 4
    assert times == sorted(times)


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


@pytest.mark.command("history")
async def test_a_turn_reads_what_its_conversation_already_holds(client: ScenarioClient) -> None:
    """A later turn reads the earlier turns' messages, tasks, answers, artifacts and a file's content."""
    first = final_task(await client.send("echo hello"))
    second = final_task(await client.send("artifacts", context_id=first.context_id))

    events = await client.send("history", context_id=first.context_id)
    read = HistoryResponse.model_validate_json(reply_texts(events)[-1])
    third = final_task(events)

    earlier = [["user", "echo hello"], ["agent", "hello"], ["user", "artifacts"], ["agent", artifacts_text()]]
    assert read.messages[:4] == earlier
    assert read.user_texts[:2] == ["echo hello", "artifacts"]
    assert read.newest_agent_text == artifacts_text()
    assert read.task_ids[:2] == [first.task_id, second.task_id]
    assert read.completed_task_ids == [first.task_id, second.task_id]
    assert read.newest_task_ids[-1] in (second.task_id, third.task_id)
    assert read.answers[first.task_id] == "hello"
    assert sorted(read.artifact_names) == sorted([ARTIFACT_DATA_NAME, ARTIFACT_FILE_NAME, ARTIFACT_URL_NAME])
    assert read.data == artifacts_data_parts()[0]
    assert read.file_text == ARTIFACT_FILE_BYTES.decode()


@pytest.mark.command("echo")
async def test_get_task_agrees_with_the_closing_event(client: ScenarioClient) -> None:
    """Reading the task back gives the state and the text the stream ended on."""
    events = await client.send("echo stored")
    closing = final_task(events)

    stored = await client.get_task(closing.task_id)

    assert stored.id == closing.task_id
    assert stored.context_id == closing.context_id
    assert stored.status.state == closing.raw.status.state
    assert stored.status.timestamp == closing.raw.status.timestamp
    assert "stored" in json.dumps(
        [part.text for message in stored.history for part in message.parts]
    )


@pytest.mark.command("echo")
async def test_the_stream_carries_nothing_after_the_terminal_task(client: ScenarioClient) -> None:
    """Nothing follows the closing Task, and the stream ends there."""
    events = await client.send("echo last")

    assert events[-1].kind == "task"
    assert_shape(events, [SKIP, task("COMPLETED")])

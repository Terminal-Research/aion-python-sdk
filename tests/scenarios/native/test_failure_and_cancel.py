"""A native agent failing or being cancelled halfway through its work.

The command-contract agents fail and are cancelled at a moment of their own
choosing, in code of ours. A real agent fails inside the model call, after
it has already streamed, and is cancelled while the model is streaming or a
tool is running - inside the framework's loop, where the adapter has to stop
it. What the client is owed there:

* a failure after the first chunks: those chunks arrived, the task is FAILED
  on the wire and in ``tasks/get``, the chunks were not kept as history, and
  nothing arrives afterwards;
* a cancel mid-stream or mid-tool: the work itself stops - watched through a
  file the model and the tool append to as they go - and the next turn in the
  context is answered.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from a2a.types import TaskState

from tests.scenarios.agents.native_script import WEATHER_PREAMBLE
from tests.scenarios.harness import ScenarioClient, eventually, final_task, reply_texts, stored_texts
from tests.scenarios.harness.recorder import Ev, _payload_of, to_event
from tests.scenarios.harness.shape import is_chunk

pytestmark = pytest.mark.native

SETTLE_SECONDS = 0.5
"""How long a stopped model or tool gets to write its last piece."""

WATCH_SECONDS = 1.0
"""How long the progress file is watched for growth after that.

Twenty times the stand-in model's delay between words: a run still going
would have written plenty by then."""


async def _failing_turn(client: ScenarioClient) -> tuple[list[Ev], str]:
    events = await client.send("fail-after 2")
    return events, final_task(events).task_id


async def test_a_model_failing_after_its_first_chunks_fails_the_task(client: ScenarioClient) -> None:
    """The chunks already sent stay sent; the outcome is FAILED on the wire."""
    events, _ = await _failing_turn(client)

    assert [event.text for event in events if is_chunk(event)] == ["word0 ", "word1 "]
    assert final_task(events).state == "FAILED"
    assert reply_texts(events) == []


async def test_the_failed_task_is_stored_as_failed_without_the_chunks(client: ScenarioClient) -> None:
    """``tasks/get`` agrees, and the partial answer is not kept as history.

    Stream deltas are transient by design (the task manager skips them when
    saving); only the caller's message and the failure remain.
    """
    _, task_id = await _failing_turn(client)

    stored = await client.get_task(task_id)

    assert stored.status.state == TaskState.TASK_STATE_FAILED
    assert not any(text.startswith("word") for text in stored_texts(stored))


async def test_nothing_arrives_after_the_failure(client: ScenarioClient) -> None:
    """The failed run does not go on behind the outcome and answer later."""
    _, task_id = await _failing_turn(client)
    before = await client.get_task(task_id)

    await asyncio.sleep(WATCH_SECONDS)
    after = await client.get_task(task_id)

    assert after.status.state == TaskState.TASK_STATE_FAILED
    assert stored_texts(after) == stored_texts(before)


async def test_a_message_completed_before_the_failure_is_kept(client: ScenarioClient) -> None:
    """What the agent had fully said stays said; only the partial answer is lost.

    The model says a sentence, calls a tool, and fails a few words into the
    answer after it. The sentence was a complete message - a durable reply on
    the wire - so the stored task keeps it, as the server keeps every such
    reply. The words after it were stream deltas, which are never stored.
    """
    events = await client.send("fail-after-tool 2")

    assert reply_texts(events) == [WEATHER_PREAMBLE]
    assert [event.text for event in events if is_chunk(event)][-2:] == ["word0 ", "word1 "]
    closing = final_task(events)
    assert closing.state == "FAILED"

    stored = await client.get_task(closing.task_id)
    assert stored.status.state == TaskState.TASK_STATE_FAILED
    assert WEATHER_PREAMBLE in stored_texts(stored)
    assert not any(text.startswith("word") for text in stored_texts(stored))


async def _start(client: ScenarioClient, text: str):
    """Open a stream and return it with the task id its first event names."""
    stream = client.stream(text)
    events: list[Ev] = []
    async for response in stream:
        event = to_event(_payload_of(response))
        events.append(event)
        if event.task_id:
            return stream, events, event.task_id, event.context_id
    raise AssertionError(f"the stream named no task: {events}")


async def _drain(stream, events: list[Ev]) -> list[Ev]:
    async for response in stream:
        events.append(to_event(_payload_of(response)))
    return events


async def _grows(path: Path) -> bool:
    """Whether anything is still writing to the progress file."""
    await asyncio.sleep(SETTLE_SECONDS)
    size = path.stat().st_size
    await asyncio.sleep(WATCH_SECONDS)
    return path.stat().st_size != size


async def _cancel_when_progressing(client: ScenarioClient, command: str, path: Path):
    stream, events, task_id, context_id = await _start(client, f"{command} {path}")

    async def progressing():
        return True if path.exists() and path.stat().st_size else None

    await eventually(progressing, what=f"{command} writing to {path}")
    cancelled = await client.cancel(task_id)
    return await _drain(stream, events), cancelled, context_id


async def test_a_cancel_stops_the_model_mid_stream(client: ScenarioClient, tmp_path: Path) -> None:
    """The model's stream stops: the progress it writes per word stops growing."""
    path = tmp_path / "model.progress"

    events, cancelled, _ = await _cancel_when_progressing(client, "slow-say", path)

    assert cancelled.status.state == TaskState.TASK_STATE_CANCELED
    assert final_task(events).state == "CANCELED"
    assert not await _grows(path)


async def test_a_cancel_stops_the_running_tool(client: ScenarioClient, tmp_path: Path) -> None:
    """The tool the framework is running stops too, not only the stream."""
    path = tmp_path / "tool.progress"

    events, cancelled, _ = await _cancel_when_progressing(client, "slow-tool", path)

    assert cancelled.status.state == TaskState.TASK_STATE_CANCELED
    assert final_task(events).state == "CANCELED"
    assert not await _grows(path)


@pytest.mark.parametrize("command", ["slow-say", "slow-tool"])
async def test_the_next_turn_after_a_cancel_is_answered(
    client: ScenarioClient, tmp_path: Path, command: str
) -> None:
    """The context is not left broken: the saved state of the cancelled run
    - a half-written checkpoint, a session with a function call and no
    response - does not stop the next turn."""
    _, _, context_id = await _cancel_when_progressing(client, command, tmp_path / "progress")

    events = await client.send("say still here", context_id=context_id)

    assert final_task(events).state == "COMPLETED"
    assert reply_texts(events) == ["still here"]

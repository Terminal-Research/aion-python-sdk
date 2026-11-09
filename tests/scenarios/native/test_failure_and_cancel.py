"""A native agent failing or being cancelled halfway through its work.

The command-contract agents fail and are cancelled at a moment of their own
choosing, in code of ours. A real agent fails inside the model call, after
it has already streamed, and is cancelled while the model is streaming or a
tool is running - inside the framework's loop, where the adapter has to stop
it. What the client is owed there:

* a failure after the first chunks: those chunks arrived, the task is FAILED
  on the wire and in ``tasks/get``, it keeps what was streamed as its
  ``aion:stream-delta`` artifact and not as a message, and nothing arrives
  afterwards;
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
from tests.scenarios.harness import (
    ASSISTANT_ANSWER_ARTIFACT_ID,
    STREAM_DELTA_ARTIFACT_ID,
    ScenarioClient,
    eventually,
    final_task,
    is_chunk,
    is_section_end,
    reply_texts,
    section_texts,
    stored_artifact,
    stored_texts,
    text_of_parts,
)
from tests.scenarios.harness.recorder import Ev, _payload_of, to_event

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
    """The chunks already streamed reach the client before the outcome, which is FAILED on the wire.

    The section never ended, so no update carries its whole text; the chunks
    the server held back to join them go out before the failure.
    """
    events, _ = await _failing_turn(client)

    assert "".join(event.text for event in events if is_chunk(event)) == "word0 word1 "
    assert section_texts(events) == []
    assert final_task(events).state == "FAILED"
    assert reply_texts(events) == []


async def test_the_failed_task_keeps_the_partial_answer_as_its_artifact(client: ScenarioClient) -> None:
    """``tasks/get`` agrees: FAILED, the partial answer in the stream-delta artifact, none of it in the history.

    A failed task gets no ``aion:assistant-answer``.
    """
    _, task_id = await _failing_turn(client)

    stored = await client.get_task(task_id)

    assert stored.status.state == TaskState.TASK_STATE_FAILED
    assert not any(text.startswith("word") for text in stored_texts(stored))
    streamed = stored_artifact(stored, STREAM_DELTA_ARTIFACT_ID)
    assert streamed is not None and text_of_parts(streamed.parts) == "word0 word1 "
    assert stored_artifact(stored, ASSISTANT_ANSWER_ARTIFACT_ID) is None


async def test_nothing_arrives_after_the_failure(client: ScenarioClient) -> None:
    """The failed run does not go on behind the outcome and answer later."""
    _, task_id = await _failing_turn(client)
    before = await client.get_task(task_id)

    await asyncio.sleep(WATCH_SECONDS)
    after = await client.get_task(task_id)

    assert after.status.state == TaskState.TASK_STATE_FAILED
    assert stored_texts(after) == stored_texts(before)


async def test_a_section_ended_before_the_failure_reached_the_client_whole(client: ScenarioClient) -> None:
    """What the model had fully said arrived whole; the task keeps the section it failed in.

    The model says a sentence, calls a tool, and fails a few words into the
    answer after it. The sentence ended its section, so its whole text
    reached the client. The answer after it opened a new section, which
    replaces the sentence in the task's stream-delta artifact: the stored
    task holds the words streamed before the failure, and no message.
    """
    events = await client.send("fail-after-tool 2")

    assert section_texts(events) == [WEATHER_PREAMBLE]
    end = next(index for index, event in enumerate(events) if is_section_end(event))
    after = [event for event in events[end + 1:] if is_chunk(event)]
    assert "".join(event.text for event in after) == "word0 word1 "
    assert after[0].append is False
    closing = final_task(events)
    assert closing.state == "FAILED"

    stored = await client.get_task(closing.task_id)
    assert stored.status.state == TaskState.TASK_STATE_FAILED
    streamed = stored_artifact(stored, STREAM_DELTA_ARTIFACT_ID)
    assert streamed is not None and text_of_parts(streamed.parts) == "word0 word1 "
    texts = stored_texts(stored)
    assert WEATHER_PREAMBLE not in texts
    assert not any(text.startswith("word") for text in texts)


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
    assert section_texts(events) == ["still here"]

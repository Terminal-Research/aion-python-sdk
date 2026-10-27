"""The agent's thoughts: the ``aion:thinking-delta`` artifact.

An agent shows how it arrives at its answer by thinking - ``Thread.thinking``
in its own code. Every thought is one text part of the task's thinking-delta
artifact: the task's first thought creates the artifact (``append`` unset),
every later one extends it (``append`` set), so the artifact keeps every
thought of the task in order. The task stores it like any other artifact. A
thought is neither a message nor a result of the task.
"""

from __future__ import annotations

import pytest

from tests.scenarios.commands import think_text, thoughts_texts
from tests.scenarios.harness import (
    THINKING_DELTA_ARTIFACT_ID,
    ScenarioClient,
    answer,
    artifact_updates,
    assert_shape,
    final_task,
    reply_texts,
    status,
    stored_artifact,
    stored_texts,
    task,
    thought,
    thought_texts,
)

pytestmark = pytest.mark.streaming


@pytest.mark.command("think")
async def test_thoughts_arrive_one_update_each_before_the_reply(client: ScenarioClient) -> None:
    """The first thought opens the artifact, every later one is appended, then the agent replies."""
    first, second, third = thoughts_texts(3)

    events = await client.send("think 3")

    assert_shape(events, [
        task("SUBMITTED"),
        status("WORKING", text=""),
        thought(text=first, append=False),
        thought(text=second, append=True),
        thought(text=third, append=True),
        status("WORKING", text=think_text()),
        answer(text=think_text()),
        task("COMPLETED"),
    ])


@pytest.mark.command("think")
async def test_every_thought_is_one_part(client: ScenarioClient) -> None:
    """One text part per update, none of them marked as the last: a thought never ends the artifact."""
    events = await client.send("think 3")

    updates = artifact_updates(events, THINKING_DELTA_ARTIFACT_ID)
    assert [update.texts for update in updates] == [(text,) for text in thoughts_texts(3)]
    assert not any(update.last_chunk for update in updates)
    assert thought_texts(events) == list(thoughts_texts(3))


@pytest.mark.command("think")
async def test_the_task_keeps_every_thought_of_the_turn(client: ScenarioClient) -> None:
    """The stored artifact holds the turn's thoughts, one part each, in order, as does the closing Task."""
    events = await client.send("think 4")
    closing = final_task(events)

    stored = await client.get_task(closing.task_id)
    for where, holder in (("tasks/get", stored), ("the closing Task", closing.raw)):
        thoughts = stored_artifact(holder, THINKING_DELTA_ARTIFACT_ID)
        assert thoughts is not None, f"{where}: no thoughts among {list(holder.artifacts)}"
        assert [part.text for part in thoughts.parts] == list(thoughts_texts(4)), where


@pytest.mark.command("think")
async def test_a_thought_is_not_a_message(client: ScenarioClient) -> None:
    """The reply is the only message of the turn, and the history holds no thought."""
    events = await client.send("think 2")

    assert reply_texts(events) == [think_text()]
    stored = await client.get_task(final_task(events).task_id)
    assert stored_texts(stored) == ["think 2", think_text()]

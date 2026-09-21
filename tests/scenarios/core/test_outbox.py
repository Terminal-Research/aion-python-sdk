"""The other door: an agent that hands the server a finished A2A payload.

Every other command speaks through the thread and lets the SDK decide what
that becomes. `a2a_outbox` is the opposite arrangement - the agent returns one
A2A object, either a Message or a Task, and the server merges it into the task
it is already keeping.

Which is why the reply is not looked for among the status events here. The
outbox is merged silently and the merged task is what closes the stream, so
the terminal Task is where a caller reads it - the same object a unary send
returns and a later `tasks/get` answers with. A scenario demanding a separate
status event would be asserting a different design.

Each case is checked in three places, because a payload can reach one and not
the others: the terminal task on the wire, the task a later `tasks/get`
returns, and the history, artifacts and metadata inside it.
"""

from __future__ import annotations

import pytest

from tests.scenarios.commands import (
    OUTBOX_MESSAGE_TEXT,
    OUTBOX_TASK_ARTIFACT_NAME,
    OUTBOX_TASK_ARTIFACT_TEXT,
    OUTBOX_TASK_HISTORY_TEXT,
    OUTBOX_TASK_METADATA,
)
from tests.scenarios.harness import ScenarioClient, final_task

pytestmark = pytest.mark.events


def _texts_of(messages) -> list[str]:
    return ["".join(part.text for part in message.parts if part.text) for message in messages]


def _history_of(task) -> list[str]:
    """What the task says was said, in order."""
    return _texts_of(task.history)


@pytest.mark.command("outbox-message")
@pytest.mark.divergence("outbox-in-history")
async def test_a_message_from_the_outbox_is_in_the_task_that_closes_the_stream(
    client: ScenarioClient,
) -> None:
    """The terminal task carries the reply, because that is where it is read.

    An agent using the outbox says nothing through the thread. The merged task
    closing the stream is the whole of what the caller receives, so a reply
    missing from it is a turn that completed without answering.
    """
    events = await client.send("outbox-message")

    closing = final_task(events)

    assert closing.state == "COMPLETED"
    assert OUTBOX_MESSAGE_TEXT in _history_of(closing.raw)


@pytest.mark.command("outbox-message")
@pytest.mark.divergence("outbox-in-history")
async def test_a_message_from_the_outbox_is_in_the_stored_task(
    client: ScenarioClient,
) -> None:
    """It is history, not a frame: `tasks/get` answers with it afterwards."""
    events = await client.send("outbox-message")
    task_id = final_task(events).task_id

    stored = await client.get_task(task_id)

    assert OUTBOX_MESSAGE_TEXT in _history_of(stored)


@pytest.mark.command("outbox-task")
async def test_a_task_patch_adds_its_artifacts(client: ScenarioClient) -> None:
    """An artifact named by the patch is on the task afterwards."""
    events = await client.send("outbox-task")
    task_id = final_task(events).task_id

    stored = await client.get_task(task_id)

    assert [artifact.name for artifact in stored.artifacts] == [OUTBOX_TASK_ARTIFACT_NAME]
    assert _texts_of(stored.artifacts) == [OUTBOX_TASK_ARTIFACT_TEXT]


@pytest.mark.command("outbox-task")
@pytest.mark.divergence("outbox-in-history")
async def test_a_task_patch_adds_its_history(client: ScenarioClient) -> None:
    """A message named by the patch is in the task's history afterwards.

    Separate from the artifact above because the two are merged by different
    routes, and one adapter loses this one: a message left in
    `status.message` reads as "being said right now" rather than as something
    the task holds, and the next reader of the history does not see it.
    """
    events = await client.send("outbox-task")
    task_id = final_task(events).task_id

    stored = await client.get_task(task_id)

    assert OUTBOX_TASK_HISTORY_TEXT in _history_of(stored)


@pytest.mark.command("outbox-task")
async def test_a_task_patch_keeps_the_inbound_message_it_is_answering(
    client: ScenarioClient,
) -> None:
    """The patch adds to history; it does not replace what was already there.

    The caller's own message is in that history. Losing it would leave a task
    that answers a question it no longer records being asked.
    """
    events = await client.send("outbox-task")
    task_id = final_task(events).task_id

    stored = await client.get_task(task_id)

    assert "outbox-task" in _history_of(stored)


@pytest.mark.command("outbox-task")
async def test_the_metadata_of_a_task_patch_is_merged(client: ScenarioClient) -> None:
    """Agent metadata lands on the task, under the agent's own keys."""
    events = await client.send("outbox-task")
    task_id = final_task(events).task_id

    stored = await client.get_task(task_id)

    for key, value in OUTBOX_TASK_METADATA.items():
        assert stored.metadata[key] == value


@pytest.mark.command("outbox-task")
async def test_the_server_keeps_the_ids_the_patch_left_unset(client: ScenarioClient) -> None:
    """Identity is the server's, whatever the payload says or omits.

    The agent builds a `Task` with no ids at all. They are not the agent's to
    choose: a patch that could set them could also point the answer at another
    task.
    """
    events = await client.send("outbox-task")
    closing = final_task(events)

    stored = await client.get_task(closing.task_id)

    assert stored.id == closing.task_id
    assert stored.context_id == closing.context_id
    assert stored.id and stored.context_id

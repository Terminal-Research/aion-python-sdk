"""A reaction to a message: the ``aion:reaction`` artifact.

An agent reacts to the message a distribution's message event names -
``Message.react`` - and the reaction reaches the client as the
``aion:reaction`` artifact, one data part carrying the ``ReactionActionPayload``
the distribution acts on. The task stores it like any other artifact. A
reaction is not a result of the task, so a task that reacted and replied
still gets its reply as ``aion:assistant-answer``.
"""

from __future__ import annotations

import pytest

from tests.scenarios.commands import REACTION_KEY, react_text
from tests.scenarios.harness import (
    MESSAGING_EXTENSION_URI,
    REACTION_ARTIFACT_ID,
    SKIP,
    EventEnvelope,
    ScenarioClient,
    answer,
    artifact,
    artifact_updates,
    assert_shape,
    data_of_parts,
    final_task,
    message_event,
    status,
    stored_artifact,
    task,
)

pytestmark = pytest.mark.events


async def _react(client: ScenarioClient) -> tuple[EventEnvelope, list]:
    envelope = message_event()
    events = await client.send(
        "react",
        message_metadata=envelope.message_metadata,
        data=[envelope.data],
        extensions=[envelope.extension_uri],
    )
    return envelope, events


def _expected_payload(envelope: EventEnvelope) -> dict:
    """The reaction the agent asks the distribution for: on the message the event named."""
    return {
        "contextId": envelope.payload["contextId"],
        "messageId": envelope.payload["messageId"],
        "reactionKey": REACTION_KEY,
        "operation": "add",
    }


@pytest.mark.command("react")
async def test_a_reaction_arrives_as_its_artifact(client: ScenarioClient) -> None:
    """One update of ``aion:reaction``: the payload for the message the event named, under the schema marker."""
    envelope, events = await _react(client)

    (update,) = artifact_updates(events, REACTION_ARTIFACT_ID)
    assert data_of_parts(update.raw.artifact.parts) == [_expected_payload(envelope)]
    marker = update.metadata.get(MESSAGING_EXTENSION_URI, {})
    assert marker.get("schema") == f"{MESSAGING_EXTENSION_URI}#ReactionActionPayload"


@pytest.mark.command("react")
async def test_the_task_keeps_the_reaction(client: ScenarioClient) -> None:
    """The stored task holds the reaction, as does the Task that closes the stream."""
    envelope, events = await _react(client)
    closing = final_task(events)

    stored = await client.get_task(closing.task_id)
    for where, holder in (("tasks/get", stored), ("the closing Task", closing.raw)):
        kept = stored_artifact(holder, REACTION_ARTIFACT_ID)
        assert kept is not None, f"{where}: no reaction among {list(holder.artifacts)}"
        assert data_of_parts(kept.parts) == [_expected_payload(envelope)], where


@pytest.mark.command("react")
async def test_a_reaction_is_not_the_task_s_result(client: ScenarioClient) -> None:
    """The reaction, then the reply, then the reply again as the task's answer."""
    _, events = await _react(client)

    assert_shape(events, [
        SKIP,
        artifact(artifact_id=REACTION_ARTIFACT_ID),
        status("WORKING", text=react_text()),
        answer(text=react_text()),
        task("COMPLETED"),
    ])

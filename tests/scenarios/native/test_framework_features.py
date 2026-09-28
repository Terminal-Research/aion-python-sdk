"""Features only one framework has, used the way that framework documents them.

A LangGraph tool that calls ``interrupt()`` to ask the user; an ADK tool that
saves an artifact through ``tool_context``, writes session state, and an
agent whose ``output_key`` keeps its last answer. Each is the framework's own
mechanism, and each has to come out of the adapter as the A2A equivalent.
"""

from __future__ import annotations

import pytest

from tests.scenarios.agents.native_script import (
    NOTED,
    REPORT_FILENAME,
    REPORT_SAVED,
    confirmed_answer,
    report_text,
)
from tests.scenarios.harness import ScenarioClient, final_task, reply_texts

pytestmark = pytest.mark.native


@pytest.mark.capability("interrupt")
async def test_a_tool_asking_the_user_pauses_the_task_for_input(client: ScenarioClient) -> None:
    """``interrupt()`` inside a tool: INPUT_REQUIRED with the question, then resume."""
    opened = final_task(await client.send("confirm deploy"))

    assert opened.state == "INPUT_REQUIRED"
    assert opened.text == "Proceed with deploy?"

    events = await client.send("yes", task_id=opened.task_id, context_id=opened.context_id)

    assert final_task(events).state == "COMPLETED"
    assert reply_texts(events) == [confirmed_answer("yes")]


@pytest.mark.capability("artifact-service")
async def test_an_artifact_a_tool_saves_reaches_the_client(client: ScenarioClient) -> None:
    """``tool_context.save_artifact`` becomes an artifact on the wire and on the task."""
    events = await client.send("report Q3")

    artifacts = [event for event in events if event.kind == "artifact" and event.artifact_name == REPORT_FILENAME]
    assert [event.text for event in artifacts] == [report_text("Q3")]
    assert reply_texts(events) == [REPORT_SAVED]
    stored = await client.get_task(final_task(events).task_id)
    assert [artifact.name for artifact in stored.artifacts] == [REPORT_FILENAME]


@pytest.mark.capability("session-state")
async def test_output_key_and_tool_state_are_in_the_next_turns_state(client: ScenarioClient) -> None:
    """What ``output_key`` and a tool wrote is there when the next turn runs.

    Read back the way an ADK agent reads its state: the instruction's
    ``{key?}`` placeholders, which ADK fills from the session on every call.
    """
    first = final_task(await client.send("remember milk"))

    events = await client.send("instruction", context_id=first.context_id)

    (answer,) = reply_texts(events)
    assert f"Last answer: {NOTED}." in answer
    assert "Note: milk." in answer

"""A pause inside a sequence of agents.

An ADK ``SequentialAgent`` runs its sub-agents one after another. When one of
them asks the user - a confirmation, any long-running tool call - the run
pauses and the task waits for input. The answer has to resume the sequence,
not only the step that asked: that step finishes, and the steps after it run,
as they do under ADK's own ``Runner``.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from tests.scenarios.agents.adk_sequence.agent import STEPS, step_answer
from tests.scenarios.conftest import serve_for
from tests.scenarios.frameworks import SEQUENCE_AGENTS, Framework
from tests.scenarios.harness import ScenarioClient, ServeVariant, final_task, reply_texts

pytestmark = [pytest.mark.native, pytest.mark.capability("agent-sequence")]


@pytest_asyncio.fixture(loop_scope="session")
async def sequence(framework: Framework):
    """A client on the framework's sequence of agents, served for the session."""
    server = serve_for(SEQUENCE_AGENTS[framework.name], ServeVariant())
    async with await ScenarioClient.connect(server.base_url, server.variant.agent_id) as client:
        yield client


async def test_the_first_step_asking_pauses_the_task(sequence: ScenarioClient) -> None:
    opened = final_task(await sequence.send("go"))

    assert opened.state == "INPUT_REQUIRED"
    assert opened.text == f"Proceed with {STEPS[0]}?"


async def test_the_answer_resumes_the_sequence_and_the_later_step_runs(sequence: ScenarioClient) -> None:
    """The step that asked finishes with the answer, and the step after it runs."""
    opened = final_task(await sequence.send("go"))

    events = await sequence.send("yes", task_id=opened.task_id, context_id=opened.context_id)

    first, second = STEPS
    assert reply_texts(events) == [step_answer(first, "yes"), step_answer(second)]
    assert final_task(events).state == "COMPLETED"


async def test_the_next_turn_runs_the_whole_sequence_again(sequence: ScenarioClient) -> None:
    """After the resumed turn the context is not left mid-sequence: a new message starts from the first step."""
    opened = final_task(await sequence.send("go"))
    done = final_task(
        await sequence.send("yes", task_id=opened.task_id, context_id=opened.context_id)
    )

    again = final_task(await sequence.send("go again", context_id=done.context_id))

    assert again.state == "INPUT_REQUIRED"

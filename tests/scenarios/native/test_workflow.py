"""An ADK Workflow served as the agent.

A workflow's nodes pass their results along as ``Event.output``, and the
run's result is the output of its terminal node. The server gives a client
what an agent's turn gives: a text result is the reply, any other value the
``structured_response`` artifact, a node's question pauses the task for
input, and an agent node's text reaches the client as any agent's does. The
results of the other nodes stay inside the run.

The agent module holds one of the workflow's agent nodes as a variable of
its own, and the path names no object: the server takes the workflow.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from tests.scenarios.agents.adk_workflow import answers
from tests.scenarios.conftest import serve_for
from tests.scenarios.frameworks import WORKFLOW_AGENTS, Framework
from tests.scenarios.harness import (
    ASSISTANT_ANSWER_ARTIFACT_ID,
    ScenarioClient,
    ServeVariant,
    artifact_updates,
    data_of_parts,
    final_task,
    reply_texts,
)

pytestmark = [pytest.mark.native, pytest.mark.capability("workflow")]


@pytest_asyncio.fixture(loop_scope="session")
async def workflow(framework: Framework):
    """A client on the framework's workflow, served for the session."""
    server = serve_for(WORKFLOW_AGENTS[framework.name], ServeVariant())
    async with await ScenarioClient.connect(server.base_url, server.variant.agent_id) as client:
        yield client


async def test_a_text_result_is_the_reply(workflow: ScenarioClient) -> None:
    events = await workflow.send("text hello world")

    assert reply_texts(events) == [answers.shout("hello world")]
    assert final_task(events).state == "COMPLETED"


async def test_any_other_result_is_the_structured_response(workflow: ScenarioClient) -> None:
    events = await workflow.send("data hello world")

    stored = await workflow.get_task(final_task(events).task_id)
    structured = [
        value for artifact in stored.artifacts if artifact.name == "structured_response"
        for value in data_of_parts(artifact.parts)
    ]
    assert structured == [answers.summary("hello world")]
    assert reply_texts(events) == []


async def test_a_nodes_question_pauses_the_task_and_the_answer_resumes_it(workflow: ScenarioClient) -> None:
    opened = final_task(await workflow.send("ask"))

    assert opened.state == "INPUT_REQUIRED"
    assert opened.text == answers.NAME_QUESTION

    events = await workflow.send("Ada", task_id=opened.task_id, context_id=opened.context_id)

    assert reply_texts(events) == [answers.greeting("Ada")]
    assert final_task(events).state == "COMPLETED"


async def test_questions_asked_at_once_are_asked_one_at_a_time(workflow: ScenarioClient) -> None:
    """Two branches ask; the task waits for each answer in turn, and the join goes on once both came."""
    opened = final_task(await workflow.send("two"))
    assert (opened.state, opened.text) == ("INPUT_REQUIRED", answers.SECOND_QUESTION)

    next_question = final_task(await workflow.send("y", task_id=opened.task_id, context_id=opened.context_id))
    assert (next_question.state, next_question.text) == ("INPUT_REQUIRED", answers.FIRST_QUESTION)

    events = await workflow.send("x", task_id=opened.task_id, context_id=opened.context_id)
    assert reply_texts(events) == [answers.both("x", "y")]
    assert final_task(events).state == "COMPLETED"


async def test_a_node_posts_through_the_aion_thread(workflow: ScenarioClient) -> None:
    events = await workflow.send("post hi")

    assert reply_texts(events) == [answers.POSTED, answers.shout("hi")]


async def test_a_terminal_none_leaves_the_last_message_as_the_answer(workflow: ScenarioClient) -> None:
    """The terminal node adds nothing, so the message a node posted is the task's answer."""
    events = await workflow.send("note hi")

    assert reply_texts(events) == [answers.noted("hi")]
    assert [update.text for update in artifact_updates(events, ASSISTANT_ANSWER_ARTIFACT_ID)] == [answers.noted("hi")]
    assert final_task(events).state == "COMPLETED"


async def test_a_terminal_none_after_nothing_said_completes_without_an_answer(workflow: ScenarioClient) -> None:
    events = await workflow.send("quiet")

    assert reply_texts(events) == []
    assert artifact_updates(events, ASSISTANT_ANSWER_ARTIFACT_ID) == []
    assert final_task(events).state == "COMPLETED"


async def test_an_agent_nodes_text_reaches_the_client_and_the_terminal_node_answers(workflow: ScenarioClient) -> None:
    events = await workflow.send("write a note")

    assert reply_texts(events) == [answers.written("a note"), f"final: {answers.written('a note')}"]


async def test_a_terminal_agent_nodes_text_is_the_reply_once(workflow: ScenarioClient) -> None:
    events = await workflow.send("draft a note")

    assert reply_texts(events) == [answers.written("a note")]
    assert final_task(events).state == "COMPLETED"

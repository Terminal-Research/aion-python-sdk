"""An agent whose work happens inside a subgraph.

A graph composed of graphs - a supervisor with sub-agents, a pipeline of
stages - does its model calls and its Aion calls one level down. LangGraph
streams what happens there under the subgraph's own namespace, and the
adapter has to take it from there: the client is owed the same events as
from a node of the parent graph.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from a2a.types import TaskState

from tests.scenarios.agents.native_script import BRANCHES, THOUGHT, branch_answer
from tests.scenarios.conftest import serve_for
from tests.scenarios.frameworks import SUBGRAPH_AGENTS, Framework
from tests.scenarios.harness import (
    ASSISTANT_ANSWER_ARTIFACT_ID,
    STREAM_DELTA_ARTIFACT_ID,
    ScenarioClient,
    ServeVariant,
    final_task,
    is_chunk,
    reply_texts,
    section_texts,
    stored_artifact,
    text_of_parts,
    thought_texts,
)

pytestmark = [pytest.mark.native, pytest.mark.capability("subgraph")]


@pytest_asyncio.fixture(loop_scope="session")
async def nested(framework: Framework):
    """A client on the framework's agent built around a subgraph, served for the session."""
    server = serve_for(SUBGRAPH_AGENTS[framework.name], ServeVariant())
    async with await ScenarioClient.connect(server.base_url, server.variant.agent_id) as client:
        yield client


async def test_a_model_inside_a_subgraph_streams_its_answer(nested: ScenarioClient) -> None:
    """The model's words arrive as they are produced, as one section, and are not sent again as a message."""
    events = await nested.send("say alpha beta gamma")

    assert [event.text for event in events if is_chunk(event)] == ["alpha ", "beta ", "gamma"]
    assert section_texts(events) == ["alpha beta gamma"]
    assert reply_texts(events) == []
    assert final_task(events).state == "COMPLETED"


async def test_the_streamed_answer_of_a_subgraph_is_the_tasks_answer(nested: ScenarioClient) -> None:
    closing = final_task(await nested.send("say alpha beta gamma"))

    stored = await nested.get_task(closing.task_id)

    assert stored.status.state == TaskState.TASK_STATE_COMPLETED
    streamed = stored_artifact(stored, STREAM_DELTA_ARTIFACT_ID)
    assert streamed is not None and text_of_parts(streamed.parts) == "alpha beta gamma"


async def test_a_reply_from_a_node_of_a_subgraph_reaches_the_client(nested: ScenarioClient) -> None:
    """``thread.reply`` one level down: the message arrives and is the task's answer."""
    events = await nested.send("reply hello from below")

    assert reply_texts(events) == ["hello from below"]
    closing = final_task(events)
    assert closing.state == "COMPLETED"

    stored = await nested.get_task(closing.task_id)
    answer = stored_artifact(stored, ASSISTANT_ANSWER_ARTIFACT_ID)
    assert answer is not None and text_of_parts(answer.parts) == "hello from below"


async def test_a_thought_from_a_node_of_a_subgraph_reaches_the_client(nested: ScenarioClient) -> None:
    events = await nested.send("reply hello from below")

    assert thought_texts(events) == [THOUGHT]


async def test_two_subgraphs_streaming_at_once_each_arrive_whole(nested: ScenarioClient) -> None:
    """Two subgraphs answer side by side: one section each, never a word of the other's in it.

    The task has one streamed answer at a time, so the second stream waits
    for the first to end and follows it as a section of its own
    (``test_parallel_streams`` holds both frameworks to that for the
    branches of one graph or agent).
    """
    events = await nested.send("both-graphs")

    assert sorted(section_texts(events)) == sorted(branch_answer(branch) for branch in BRANCHES)
    assert reply_texts(events) == []
    assert final_task(events).state == "COMPLETED"

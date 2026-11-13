"""Two models streaming at once.

A fan-out - parallel nodes of a graph, the sub-agents of an ADK
``ParallelAgent`` - runs model calls side by side, and their chunks reach the
adapter interleaved. A task has one streamed answer at a time, so the streams
take turns: the one that started first goes out live, the other waits and
follows as a section of its own. Each answer reaches the client whole, and
the task keeps the one that ended last, as after two model calls in a row.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from tests.scenarios.agents.native_script import BRANCHES, branch_answer
from tests.scenarios.conftest import serve_for
from tests.scenarios.frameworks import PARALLEL_AGENTS, Framework
from tests.scenarios.harness import (
    STREAM_DELTA_ARTIFACT_ID,
    ScenarioClient,
    ServeVariant,
    final_task,
    reply_texts,
    section_texts,
    stored_artifact,
    text_of_parts,
)

pytestmark = pytest.mark.native

COMMAND = "both"


@pytest_asyncio.fixture(loop_scope="session")
async def parallel(framework: Framework):
    """A client on the framework's agent with two branches, served for the session."""
    server = serve_for(PARALLEL_AGENTS[framework.name], ServeVariant())
    async with await ScenarioClient.connect(server.base_url, server.variant.agent_id) as client:
        yield client


async def test_two_models_streaming_at_once_each_arrive_whole(parallel: ScenarioClient) -> None:
    """One section per branch, never a word of the other's in it, and nothing sent twice."""
    events = await parallel.send(COMMAND)

    assert sorted(section_texts(events)) == sorted(branch_answer(branch) for branch in BRANCHES)
    assert reply_texts(events) == []
    assert final_task(events).state == "COMPLETED"


async def test_the_task_keeps_the_last_of_two_streamed_answers_whole(parallel: ScenarioClient) -> None:
    """The stored stream-delta is the section that ended last."""
    events = await parallel.send(COMMAND)

    stored = await parallel.get_task(final_task(events).task_id)

    streamed = stored_artifact(stored, STREAM_DELTA_ARTIFACT_ID)
    assert streamed is not None and text_of_parts(streamed.parts) == section_texts(events)[-1]

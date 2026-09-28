"""The agent ``langchain.agents.create_agent`` builds, served as it is built.

Most LangGraph agents are not assembled node by node: they come from
``create_agent``, compiled, and in the form a guide shows first - with no
checkpointer of their own. The server needs one to finish a turn at all (it
reads the graph's state at the end of every turn), and the agent needs one
for memory and for ``interrupt()``. So the server supplies its own, and these
scenarios hold it to that: the turn completes, the next one remembers, and a
tool can pause the run.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from tests.scenarios.agents.native_script import (
    RECALL_PREFIX,
    WEATHER_PREAMBLE,
    confirmed_answer,
    weather_answer,
)
from tests.scenarios.conftest import serve_for
from tests.scenarios.frameworks import PREBUILT_AGENTS, Framework
from tests.scenarios.harness import ScenarioClient, ServeVariant, final_task, reply_texts

pytestmark = [pytest.mark.native, pytest.mark.capability("prebuilt-agent")]


@pytest_asyncio.fixture(loop_scope="session")
async def prebuilt(framework: Framework):
    """A client on the framework's prebuilt agent, served for the session."""
    server = serve_for(PREBUILT_AGENTS[framework.name], ServeVariant())
    async with await ScenarioClient.connect(server.base_url, server.variant.agent_id) as client:
        yield client


async def test_a_turn_with_a_tool_completes(prebuilt: ScenarioClient) -> None:
    events = await prebuilt.send("weather Kyiv")

    assert final_task(events).state == "COMPLETED"
    assert reply_texts(events) == [WEATHER_PREAMBLE, weather_answer("Kyiv")]


async def test_the_next_turn_remembers(prebuilt: ScenarioClient) -> None:
    first = final_task(await prebuilt.send("say hello"))

    events = await prebuilt.send("recall", context_id=first.context_id)

    assert reply_texts(events) == [RECALL_PREFIX + "say hello"]


async def test_a_tool_can_pause_for_the_user(prebuilt: ScenarioClient) -> None:
    opened = final_task(await prebuilt.send("confirm deploy"))
    assert opened.state == "INPUT_REQUIRED"

    events = await prebuilt.send("yes", task_id=opened.task_id, context_id=opened.context_id)

    assert final_task(events).state == "COMPLETED"
    assert reply_texts(events) == [confirmed_answer("yes")]

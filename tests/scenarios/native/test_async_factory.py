"""An agent built by an ``async def`` factory.

A factory often has to await while it builds the agent - loading the tools of
an MCP server is the usual reason. The path in ``aion.yaml`` then names an
``async def``, and the server awaits it before it serves: the agent it
returns runs like any other.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from tests.scenarios.agents.native_script import WEATHER_PREAMBLE, weather_answer
from tests.scenarios.conftest import serve_for
from tests.scenarios.frameworks import ASYNC_FACTORY_AGENTS, Framework
from tests.scenarios.harness import ScenarioClient, ServeVariant, final_task, section_texts

pytestmark = [pytest.mark.native]


@pytest_asyncio.fixture(loop_scope="session")
async def built_async(framework: Framework):
    """A client on the framework's agent from an async factory, served for the session."""
    server = serve_for(ASYNC_FACTORY_AGENTS[framework.name], ServeVariant())
    async with await ScenarioClient.connect(server.base_url, server.variant.agent_id) as client:
        yield client


async def test_the_agent_of_an_async_factory_is_served(built_async: ScenarioClient) -> None:
    events = await built_async.send("weather Kyiv")

    assert final_task(events).state == "COMPLETED"
    assert section_texts(events) == [WEATHER_PREAMBLE, weather_answer("Kyiv")]

"""An ADK ``App`` served as the agent, with its plugins.

An ADK project often defines its agent and the ``App`` that holds it - with
its plugins, events compaction and context cache - side by side. The server
takes the App when the path in ``aion.yaml`` names neither, as ADK's loader
does, and runs the agent with what the App holds.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from tests.scenarios.agents.adk_app.agent import MARK, answer
from tests.scenarios.conftest import serve_for
from tests.scenarios.frameworks import APP_AGENTS, Framework
from tests.scenarios.harness import ScenarioClient, ServeVariant, final_task, reply_texts

pytestmark = [pytest.mark.native, pytest.mark.capability("app")]


@pytest_asyncio.fixture(loop_scope="session")
async def served_app(framework: Framework):
    """A client on the framework's App with a plugin, served for the session."""
    server = serve_for(APP_AGENTS[framework.name], ServeVariant())
    async with await ScenarioClient.connect(server.base_url, server.variant.agent_id) as client:
        yield client


async def test_the_apps_plugin_runs_with_its_agent(served_app: ScenarioClient) -> None:
    events = await served_app.send("hello")

    assert reply_texts(events) == [answer("hello") + MARK]
    assert final_task(events).state == "COMPLETED"

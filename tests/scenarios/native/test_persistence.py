"""A native agent's memory and pause live in the database, not in the process.

The conversation a native agent remembers is the framework's own - the
LangGraph checkpoint, the ADK session - and a paused LangGraph run is a
checkpoint too. With ``POSTGRES_URL`` set, all of it is in PostgreSQL, so a
new server process on the same database has to find it: the next turn after
a restart still sees the earlier ones, and a task paused by a tool's
``interrupt()`` resumes from where it stopped.
"""

from __future__ import annotations

from typing import Iterator

import pytest

from tests.scenarios.agents.native_script import RECALL_PREFIX, confirmed_answer
from tests.scenarios.frameworks import PREBUILT_AGENTS, Framework
from tests.scenarios.harness import ScenarioClient, ServeProcess, ServeVariant, final_task, reply_texts
from tests.scenarios.harness.pg import have_postgres, postgres_env

pytestmark = [pytest.mark.native, pytest.mark.persistence]


@pytest.fixture(autouse=True)
def needs_postgres() -> None:
    """Skip without a database, as the persistence directory does."""
    if not have_postgres():
        pytest.skip("POSTGRES_TEST_URL is not set")


@pytest.fixture
def pg_server(framework: Framework) -> Iterator[ServeProcess]:
    """The native agent on PostgreSQL, a process of this test's own."""
    process = ServeProcess(framework, ServeVariant(name="default", env=postgres_env()))
    try:
        yield process.start()
    finally:
        process.stop()


@pytest.fixture
def pg_prebuilt_server(framework: Framework) -> Iterator[ServeProcess]:
    """The framework's prebuilt agent on PostgreSQL."""
    process = ServeProcess(PREBUILT_AGENTS[framework.name], ServeVariant(name="default", env=postgres_env()))
    try:
        yield process.start()
    finally:
        process.stop()


async def _connect(server: ServeProcess) -> ScenarioClient:
    return await ScenarioClient.connect(server.base_url, server.variant.agent_id)


async def test_the_conversation_survives_a_restart(pg_server: ServeProcess) -> None:
    async with await _connect(pg_server) as client:
        first = final_task(await client.send("say hello"))

    pg_server.restart()

    async with await _connect(pg_server) as client:
        events = await client.send("recall", context_id=first.context_id)

    assert reply_texts(events) == [RECALL_PREFIX + "say hello"]


@pytest.mark.capability("interrupt")
async def test_a_paused_tool_resumes_after_a_restart(pg_server: ServeProcess) -> None:
    """The run paused inside ``interrupt()`` continues from the PG checkpoint."""
    async with await _connect(pg_server) as client:
        opened = final_task(await client.send("confirm deploy"))
    assert opened.state == "INPUT_REQUIRED"

    pg_server.restart()

    async with await _connect(pg_server) as client:
        events = await client.send("yes", task_id=opened.task_id, context_id=opened.context_id)

    assert final_task(events).state == "COMPLETED"
    assert reply_texts(events) == [confirmed_answer("yes")]


@pytest.mark.capability("prebuilt-agent")
async def test_a_prebuilt_agents_conversation_survives_a_restart(pg_prebuilt_server: ServeProcess) -> None:
    """``create_agent`` brings no checkpointer; the one it runs with is the
    server's PostgreSQL one, so its memory outlives the process too."""
    async with await _connect(pg_prebuilt_server) as client:
        first = final_task(await client.send("say hello"))

    pg_prebuilt_server.restart()

    async with await _connect(pg_prebuilt_server) as client:
        events = await client.send("recall", context_id=first.context_id)

    assert reply_texts(events) == [RECALL_PREFIX + "say hello"]

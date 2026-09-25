"""A native agent served by two instances over one database.

Any instance may take the next turn of a context, so what the agent
remembers and where it paused cannot live in the instance that served the
last one. Two servers of the native agent, one PostgreSQL: a conversation
begun on one continues on the other, and a tool paused on one resumes on the
other.
"""

from __future__ import annotations

from typing import Iterator

import pytest

from tests.scenarios.agents.native_script import RECALL_PREFIX, confirmed_answer
from tests.scenarios.frameworks import Framework
from tests.scenarios.harness import ScenarioClient, ServeProcess, ServeVariant, final_task, reply_texts
from tests.scenarios.harness.pg import claim_released, have_postgres, postgres_env

pytestmark = [pytest.mark.native, pytest.mark.distributed]


@pytest.fixture(scope="module")
def servers(framework: Framework) -> Iterator[tuple[ServeProcess, ServeProcess]]:
    """Two servers of the native agent on one database.

    The database check is here rather than in a function-scoped autouse
    fixture: a module-scoped fixture is set up before those run, and would
    fail on the missing database instead of skipping.
    """
    if not have_postgres():
        pytest.skip("POSTGRES_TEST_URL is not set")
    processes = [
        ServeProcess(framework, ServeVariant(name="default", env={**postgres_env(), "HOST_NAME": host}))
        for host in ("native-host-a", "native-host-b")
    ]
    try:
        for process in processes:
            process.start()
        yield processes[0], processes[1]
    finally:
        for process in processes:
            process.stop()


async def _connect(server: ServeProcess) -> ScenarioClient:
    return await ScenarioClient.connect(server.base_url, server.variant.agent_id)


async def test_a_conversation_begun_on_one_instance_continues_on_the_other(
    servers: tuple[ServeProcess, ServeProcess],
) -> None:
    first_server, second_server = servers
    async with await _connect(first_server) as client:
        first = final_task(await client.send("say hello"))
    await claim_released(first.task_id)

    async with await _connect(second_server) as client:
        events = await client.send("recall", context_id=first.context_id)

    assert reply_texts(events) == [RECALL_PREFIX + "say hello"]


@pytest.mark.capability("interrupt")
async def test_a_tool_paused_on_one_instance_resumes_on_the_other(
    servers: tuple[ServeProcess, ServeProcess],
) -> None:
    first_server, second_server = servers
    async with await _connect(first_server) as client:
        opened = final_task(await client.send("confirm deploy"))
    assert opened.state == "INPUT_REQUIRED"
    await claim_released(opened.task_id)

    async with await _connect(second_server) as client:
        events = await client.send("yes", task_id=opened.task_id, context_id=opened.context_id)

    assert final_task(events).state == "COMPLETED"
    assert reply_texts(events) == [confirmed_answer("yes")]

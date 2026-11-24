"""An ADK Workflow's question outlives a restart of a server backed by PostgreSQL.

ADK rebuilds a paused workflow from the events of its session, which the
server keeps in PostgreSQL. A node asks, the server restarts, and the
answer sent to the new process resumes the workflow from the stored
session: the next node greets the user by the name they gave.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from packaging.version import Version

from tests.scenarios.agents.adk_workflow import answers
from tests.scenarios.frameworks import WORKFLOW_AGENTS, Framework, native_since, native_unsupported_reason
from tests.scenarios.harness import ScenarioClient, ServeProcess, ServeVariant, final_task, installed_version, reply_texts

pytestmark = [pytest.mark.persistence]

CAPABILITY = "workflow"


@pytest.fixture
def workflow_server(framework: Framework, pg_variant: ServeVariant) -> Iterator[ServeProcess]:
    """The framework's workflow on a server backed by Postgres; skipped where the framework has none."""
    reason = native_unsupported_reason(framework.name, CAPABILITY)
    if reason:
        pytest.skip(reason)
    since = native_since(framework.name, CAPABILITY)
    installed = installed_version(since.distribution) if since is not None else None
    if since is not None and installed is not None and Version(installed) < Version(since.version):
        pytest.skip(f"{since.distribution} {installed} on the server: {since.reason}")
    process = ServeProcess(WORKFLOW_AGENTS[framework.name], pg_variant)
    try:
        yield process.start()
    finally:
        process.stop()


async def test_a_nodes_question_is_answered_after_a_restart(workflow_server: ServeProcess) -> None:
    async with await ScenarioClient.connect(workflow_server.base_url, workflow_server.variant.agent_id) as client:
        opened = final_task(await client.send("ask"))
    assert (opened.state, opened.text) == ("INPUT_REQUIRED", answers.NAME_QUESTION)

    workflow_server.restart()

    async with await ScenarioClient.connect(workflow_server.base_url, workflow_server.variant.agent_id) as client:
        events = await client.send("Ada", task_id=opened.task_id, context_id=opened.context_id)

    assert reply_texts(events) == [answers.greeting("Ada")]
    assert final_task(events).state == "COMPLETED"

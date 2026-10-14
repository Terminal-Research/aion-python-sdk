"""A toolkit's tasks across the servers of one agent.

Three ``aion serve`` over one database serve the same agent: two with the
scripted toolkits installed, one without them - the shape of a rollout in
which not every server runs the same image yet.

* One task per context holds across servers: the check that refuses a
  second task of an exclusive toolkit reads the store, so the server that
  receives the second directive refuses it with ``context_busy`` naming the
  task another server is running, and so does a message into that task.
* A toolkit's task is served by the toolkit and by nothing else: a server
  without it refuses a message into the task with ``extension_unavailable``,
  and the task is left as it was, rather than handed to the agent's framework.
"""

from __future__ import annotations

import contextlib
import dataclasses
import uuid
from typing import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from a2a.types import TaskState

from tests.scenarios.frameworks import Framework
from tests.scenarios.harness import ScenarioClient, ServeProcess, ServeVariant
from tests.scenarios.harness.pg import postgres_env
from tests.scenarios.toolkits import COMPLETED, EXCLUSIVE_URI, TOOLKIT, rpc_script, run_script, start_working
from tests.support.scripted_toolkit.contract import URI

pytestmark = [pytest.mark.distributed]

INVALID_PARAMS = -32602

HOSTS = ("scenario-toolkit-host-a", "scenario-toolkit-host-b", "scenario-plain-host")


@pytest.fixture(scope="module")
def pods(framework: Framework) -> Iterator[tuple[ServeProcess, ServeProcess, ServeProcess]]:
    """Two servers with the toolkits and one without them, on one database."""
    toolkit_hosts, plain_host = HOSTS[:2], HOSTS[2]
    variants = [
        dataclasses.replace(TOOLKIT, env={**TOOLKIT.env, **postgres_env(), "HOST_NAME": host})
        for host in toolkit_hosts
    ]
    variants.append(ServeVariant(name="default", env={**postgres_env(), "HOST_NAME": plain_host}))
    processes = [ServeProcess(framework, variant) for variant in variants]
    try:
        for process in processes:
            process.start()
        yield processes[0], processes[1], processes[2]
    finally:
        for process in processes:
            process.stop()


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def pod_clients(
    pods: tuple[ServeProcess, ServeProcess, ServeProcess],
) -> AsyncIterator[tuple[ScenarioClient, ScenarioClient, ScenarioClient]]:
    """One client per server, each through that server's own proxy, all one caller."""
    clients = [await ScenarioClient.connect(pod.base_url, pod.variant.agent_id) for pod in pods]
    try:
        yield clients[0], clients[1], clients[2]
    finally:
        for client in clients:
            await client.close()


def _refusal(response: dict) -> dict:
    """The ``ErrorInfo`` metadata of an ``InvalidParams`` refusal."""
    error = response.get("error")
    assert error is not None, f"the request was served: {response}"
    assert error["code"] == INVALID_PARAMS, error
    return error["data"][0]["metadata"]


async def test_a_busy_context_is_refused_on_the_other_server(
    pod_clients: tuple[ScenarioClient, ScenarioClient, ScenarioClient],
) -> None:
    """The second server refuses a second task, and a message into the running one, with ``context_busy``."""
    first, second, _ = pod_clients
    context_id = f"exclusive-across-{uuid.uuid4().hex}"
    task_id, stream = await start_working(first, EXCLUSIVE_URI, context_id=context_id)
    try:
        directive = await rpc_script(second, EXCLUSIVE_URI, context_id=context_id, outputs=[COMPLETED])
        into_the_task = await rpc_script(second, EXCLUSIVE_URI, task_id=task_id, outputs=[COMPLETED])

        busy = {"code": "context_busy", "retryable": "true", "taskId": task_id}
        assert _refusal(directive) == busy
        assert _refusal(into_the_task) == busy

        assert (await first.cancel(task_id)).status.state == TaskState.TASK_STATE_CANCELED
    finally:
        with contextlib.suppress(Exception):
            await stream.aclose()

    after = await run_script(second, EXCLUSIVE_URI, context_id=context_id, outputs=[COMPLETED])
    assert after.status.state == TaskState.TASK_STATE_COMPLETED


async def test_a_server_without_the_toolkit_refuses_a_message_into_its_task(
    pod_clients: tuple[ScenarioClient, ScenarioClient, ScenarioClient],
) -> None:
    """``extension_unavailable``, and the waiting task keeps its state and its history."""
    with_toolkit, _, without = pod_clients
    waiting = await run_script(with_toolkit, URI, outputs=[{"kind": "result", "state": "input-required"}])

    response = await without.rpc(
        "SendMessage",
        {
            "message": {
                "messageId": uuid.uuid4().hex,
                "taskId": waiting.id,
                "role": "ROLE_USER",
                "parts": [{"text": "go on"}],
            }
        },
    )

    assert _refusal(response) == {"code": "extension_unavailable", "retryable": "true"}
    assert await without.get_task(waiting.id) == waiting

"""Evolution tasks across the servers of one agent.

Three ``aion serve`` over one database serve the same agent: two with the
evolution extension enabled and the stand-in toolkit installed, one without
the extension - the shape of a rollout in which not every server runs the
same image yet.

* One task per context holds across servers: the check that rejects a
  second evolution task reads the store, so the server that receives the
  second directive rejects it with ``context_busy`` naming the task another
  server is running, and refuses a message into that task with
  ``task_busy``.
* An evolution task is served by the extension and by nothing else: a server
  without it refuses a message into the task with ``extension_unavailable``,
  and the task is left as it was, rather than handed to the agent's framework.
* A cancel sent to another server is answered at once with ``CANCELED``. The
  server running the turn learns of it at its next write, which meets a
  version conflict: its stream closes ``CANCELED``, the toolkit is asked to
  stop, and nothing more is written into the task.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import uuid
from typing import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from a2a.types import TaskState
from google.protobuf.json_format import MessageToDict

from tests.scenarios.frameworks import Framework
from tests.scenarios.harness import ScenarioClient, ServeProcess, ServeVariant, final_task
from tests.scenarios.harness.pg import postgres_env
from tests.scenarios.evolution import (
    COMPLETED,
    EVOLUTION,
    rejection,
    reported,
    rest_of,
    rpc_script,
    run_script,
    start_working,
)

pytestmark = [pytest.mark.distributed]

INVALID_PARAMS = -32602

HOSTS = ("scenario-evolution-host-a", "scenario-evolution-host-b", "scenario-plain-host")


@pytest.fixture(scope="module")
def pods(framework: Framework) -> Iterator[tuple[ServeProcess, ServeProcess, ServeProcess]]:
    """Two servers with the evolution extension and one without it, on one database."""
    evolution_hosts, plain_host = HOSTS[:2], HOSTS[2]
    variants = [
        dataclasses.replace(EVOLUTION, env={**EVOLUTION.env, **postgres_env(), "HOST_NAME": host})
        for host in evolution_hosts
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
    """The second server rejects a second task with ``context_busy``, and a message into the running one with ``task_busy``."""
    first, second, _ = pod_clients
    context_id = f"exclusive-across-{uuid.uuid4().hex}"
    task_id, stream = await start_working(first, context_id=context_id)
    try:
        directive = await rpc_script(second, context_id=context_id, outputs=[COMPLETED])
        into_the_task = await rpc_script(second, task_id=task_id, outputs=[COMPLETED])

        refused = rejection(directive)
        assert (refused["code"], refused["retryable"]) == ("context_busy", True)
        assert task_id in refused["message"]
        assert _refusal(into_the_task) == {"code": "task_busy", "retryable": "true", "taskId": task_id}

        assert (await first.cancel(task_id)).status.state == TaskState.TASK_STATE_CANCELED
    finally:
        with contextlib.suppress(Exception):
            await stream.aclose()

    after = await run_script(second, context_id=context_id, outputs=[COMPLETED])
    assert after.status.state == TaskState.TASK_STATE_COMPLETED


async def test_a_server_without_the_extension_refuses_a_message_into_its_task(
    pod_clients: tuple[ScenarioClient, ScenarioClient, ScenarioClient],
) -> None:
    """``extension_unavailable``, and the waiting task keeps its state and its history."""
    with_extension, _, without = pod_clients
    waiting = await run_script(with_extension, outputs=[{"kind": "status", "state": "input-required"}])

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


async def test_a_cancel_on_the_other_server_reaches_the_executor_at_the_owners_next_write(
    pod_clients: tuple[ScenarioClient, ScenarioClient, ScenarioClient],
) -> None:
    """``CANCELED`` at once from the other server; the owner's stream closes with it and its executor is asked to stop."""
    owner, other, _ = pod_clients
    task_id, stream = await start_working(owner, tick_seconds=0.5)

    answered = await other.cancel(task_id)
    streamed = await rest_of(stream)

    assert answered.status.state == TaskState.TASK_STATE_CANCELED
    assert final_task(streamed).state == "CANCELED"
    asked = []
    for _ in range(20):
        asked = [item for item in (await reported(owner))["cancels"] if item["task_id"] == task_id]
        if asked:
            break
        await asyncio.sleep(0.25)
    assert asked == [{"task_id": task_id, "within": None}]

    settled = await owner.get_task(task_id)
    await asyncio.sleep(1.5)
    later = await other.get_task(task_id)
    assert later.status.state == TaskState.TASK_STATE_CANCELED
    assert MessageToDict(later.metadata) == MessageToDict(settled.metadata), "the turn wrote after the cancel"

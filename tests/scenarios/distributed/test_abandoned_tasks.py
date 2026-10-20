"""Tasks their server abandoned - hung or dead - settled by the other server of the agent.

Two ``aion serve`` with the evolution extension and its stand-in toolkit run over one database with a
small ``AION_STALE_TASK_TIMEOUT``, as an agent of their own: the other
distributed scenarios share the database, and a sweep reads its own agent's
tasks only. A task that stays ``SUBMITTED`` or
``WORKING`` without a durable write for longer than that is taken for
abandoned and settled ``FAILED`` with ``aion:settledReason=task_abandoned``;
an executable extension's task also carries the error ``task_abandoned`` in
the shape of an extension's failure.

* A turn that hangs on one server is left alone by that server - it is
  alive there - and settled by the other one's background sweep, with no
  request needed; a task waiting for input is never touched.
* A context an abandoned evolution task held takes a new task
  again, and a message into the abandoned task is refused like one into a
  finished task.
* A server killed outright leaves its tasks - an evolution's and the
  framework's - to the survivor, which settles them and admits the next
  task of the context.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import time
import uuid
from typing import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from a2a.types import TaskState
from google.protobuf.json_format import MessageToDict

from tests.scenarios.frameworks import Framework
from tests.scenarios.harness import ScenarioClient, ServeProcess, run_until_working
from tests.scenarios.harness.pg import postgres_env
from tests.scenarios.evolution import (
    COMPLETED,
    EVOLUTION,
    data_parts,
    rpc_script,
    run_script,
    start_working,
)

pytestmark = [pytest.mark.distributed]

STALE_SECONDS = 3
"""The servers' ``AION_STALE_TASK_TIMEOUT``; they sweep every half of it."""

SETTLED_WITHIN = 20
"""How long a scenario waits for an abandoned task to be settled: the timeout, a sweep, and room."""

HOSTS = ("scenario-abandoned-host-a", "scenario-abandoned-host-b")

AGENT_ID = "scenario-abandoned-agent"
"""An agent no other scenario serves, so the small timeout settles none of their tasks."""

SETTLED_REASON_KEY = "aion:settledReason"

SLOW_SECONDS = 300
"""Far longer than the whole scenario, so the agent never ends a task itself."""


def _pair(framework: Framework) -> Iterator[tuple[ServeProcess, ServeProcess]]:
    """Two servers with the evolution extension on one database, with the small timeout; taken away afterwards."""
    processes = [
        ServeProcess(
            framework,
            dataclasses.replace(
                EVOLUTION,
                agent_ids=(AGENT_ID,),
                env={
                    **EVOLUTION.env,
                    **postgres_env(),
                    "HOST_NAME": host,
                    "AION_STALE_TASK_TIMEOUT": str(STALE_SECONDS),
                },
            ),
        )
        for host in HOSTS
    ]
    try:
        for process in processes:
            process.start()
        yield processes[0], processes[1]
    finally:
        for process in processes:
            process.stop()


@pytest.fixture(scope="module")
def pair(framework: Framework) -> Iterator[tuple[ServeProcess, ServeProcess]]:
    yield from _pair(framework)


@pytest.fixture
def fresh_pair(framework: Framework) -> Iterator[tuple[ServeProcess, ServeProcess]]:
    """A pair of this test's own, for a scenario that kills one of them."""
    yield from _pair(framework)


async def _clients(servers: tuple[ServeProcess, ServeProcess]) -> tuple[ScenarioClient, ScenarioClient]:
    return (
        await ScenarioClient.connect(servers[0].base_url, servers[0].variant.agent_id),
        await ScenarioClient.connect(servers[1].base_url, servers[1].variant.agent_id),
    )


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def clients(pair) -> AsyncIterator[tuple[ScenarioClient, ScenarioClient]]:
    connected = await _clients(pair)
    try:
        yield connected
    finally:
        for client in connected:
            await client.close()


@pytest_asyncio.fixture(loop_scope="session")
async def fresh_clients(fresh_pair) -> AsyncIterator[tuple[ScenarioClient, ScenarioClient]]:
    connected = await _clients(fresh_pair)
    try:
        yield connected
    finally:
        for client in connected:
            await client.close()


async def settled(client: ScenarioClient, task_id: str):
    """The task once it has an outcome; fails after ``SETTLED_WITHIN`` seconds."""
    deadline = time.monotonic() + SETTLED_WITHIN
    while True:
        stored = await client.get_task(task_id)
        if stored.status.state not in (TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_WORKING):
            return stored
        assert time.monotonic() < deadline, f"task {task_id} is still {TaskState.Name(stored.status.state)}"
        await asyncio.sleep(0.5)


def assert_abandoned(stored, *, extension: bool) -> None:
    """``FAILED`` with the reason, and for an extension's task the extension error ``task_abandoned``."""
    assert stored.status.state == TaskState.TASK_STATE_FAILED
    assert MessageToDict(stored.metadata)[SETTLED_REASON_KEY] == "task_abandoned"
    errors = [data for data in data_parts(stored) if data.get("code") == "task_abandoned"]
    assert bool(errors) == extension, data_parts(stored)
    if extension:
        assert errors[-1]["retryable"] is True


async def test_a_hung_turn_is_settled_by_the_other_servers_sweep_and_a_waiting_task_is_left(
    clients: tuple[ScenarioClient, ScenarioClient],
) -> None:
    """No request is needed; the task waiting for input outlives the timeout untouched."""
    owner, other = clients
    waiting = await run_script(owner, outputs=[{"kind": "status", "state": "input-required"}])
    task_id, stream = await start_working(owner)
    with contextlib.suppress(Exception):
        await stream.aclose()

    assert_abandoned(await settled(other, task_id), extension=True)
    assert (await other.get_task(waiting.id)).status.state == TaskState.TASK_STATE_INPUT_REQUIRED


async def test_an_abandoned_task_frees_its_context_and_takes_no_message(
    clients: tuple[ScenarioClient, ScenarioClient],
) -> None:
    """Past the timeout the context of an evolution takes a new task; the old task refuses a message."""
    owner, other = clients
    context_id = f"abandoned-{uuid.uuid4().hex}"
    task_id, stream = await start_working(owner, context_id=context_id)
    with contextlib.suppress(Exception):
        await stream.aclose()
    await asyncio.sleep(STALE_SECONDS + 0.5)

    into_the_task = await rpc_script(other, task_id=task_id, outputs=[COMPLETED])
    after = await run_script(other, context_id=context_id, outputs=[COMPLETED])

    assert "terminal state" in into_the_task["error"]["message"], into_the_task
    assert_abandoned(await other.get_task(task_id), extension=True)
    assert after.status.state == TaskState.TASK_STATE_COMPLETED


@pytest.mark.command("slow")
async def test_the_tasks_of_a_server_killed_mid_turn_are_settled_by_the_survivor(
    fresh_pair: tuple[ServeProcess, ServeProcess],
    fresh_clients: tuple[ScenarioClient, ScenarioClient],
) -> None:
    """An evolution turn and a framework turn die with their server; the survivor settles both and serves on."""
    dead_server, _ = fresh_pair
    owner, survivor = fresh_clients
    context_id = f"killed-{uuid.uuid4().hex}"
    evolution_task, stream = await start_working(owner, context_id=context_id, tick_seconds=0.5)
    with contextlib.suppress(Exception):
        await stream.aclose()
    framework_task, _ = await run_until_working(owner, f"slow {SLOW_SECONDS}")

    dead_server.kill()

    assert_abandoned(await settled(survivor, evolution_task), extension=True)
    assert_abandoned(await settled(survivor, framework_task), extension=False)
    after = await run_script(survivor, context_id=context_id, outputs=[COMPLETED])
    assert after.status.state == TaskState.TASK_STATE_COMPLETED

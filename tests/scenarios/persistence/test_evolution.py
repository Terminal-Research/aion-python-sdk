"""What an evolution task keeps across a server restart, on a real database.

The executor runs in the server process, so a restart replaces it with a new
instance that knows nothing the old one did. What an executor relies on
between tasks therefore has to come from the store: the artifact versions of
its context, which the next task numbers from and reads back.

What the stand-in toolkit was handed is read from the report a turn makes,
since the server is another process.

An orderly shutdown asks every running turn to stop first: the turn reports
what it kept, and the server closes the task ``FAILED`` coded
``server_shutdown``, which closes the stream a client has open on it too. A
turn that does not stop within the window is settled by the drain instead,
and a stream open on any task does not hold the shutdown past its bounded
wait. ``AION_SHUTDOWN_TIMEOUT`` sizes those waits: with a small budget the
executor is told the shorter window it gets in ``cancel()``, and a turn that
outlasts it is settled sooner.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import time
import uuid

import pytest
from a2a.types import TaskState
from google.protobuf.json_format import MessageToDict

from tests.scenarios.frameworks import Framework
from tests.scenarios.harness import ScenarioClient, ServeProcess, ServeVariant, final_task
from tests.scenarios.harness.pg import postgres_env
from tests.scenarios.harness.recorder import _payload_of, to_event
from tests.scenarios.evolution import (
    COMPLETED,
    EVOLUTION,
    URI,
    data_parts,
    rest_of,
    run_script,
    start_working,
)

pytestmark = [pytest.mark.persistence]

SPEC = "evolution-spec"

SETTLED_REASON_KEY = "aion:settledReason"
"""Task metadata saying the server, not the executor, decided the final state."""

SHUTDOWN_ERROR = {
    "code": "server_shutdown",
    "message": "The server stopped while the task was running; send the request again.",
    "retryable": True,
}
"""The error the server closes a turn it stopped for a shutdown with."""

SMALL_SHUTDOWN_BUDGET = "5"
"""An ``AION_SHUTDOWN_TIMEOUT`` small enough that every step of the shutdown shrinks."""

SMALL_BUDGET_TURN_WINDOW = 3.0
"""What the running turns get of ``SMALL_SHUTDOWN_BUDGET``: 5 s less 1 s graceful, 0.4 s settlement, 0.6 s cleanup."""


@pytest.fixture
def pg_variant() -> ServeVariant:
    """The evolution deployment with a Postgres backend."""
    return dataclasses.replace(EVOLUTION, env={**EVOLUTION.env, **postgres_env()})


@pytest.fixture
def small_budget_server(framework: Framework, pg_variant: ServeVariant):
    """The evolution deployment on Postgres with ``AION_SHUTDOWN_TIMEOUT`` set to ``SMALL_SHUTDOWN_BUDGET``."""
    variant = dataclasses.replace(pg_variant, env={**pg_variant.env, "AION_SHUTDOWN_TIMEOUT": SMALL_SHUTDOWN_BUDGET})
    process = ServeProcess(framework, variant)
    try:
        yield process.start()
    finally:
        process.stop()


async def reconnect(server: ServeProcess) -> ScenarioClient:
    """A client on the server that is running now, whichever process that is."""
    return await ScenarioClient.connect(server.base_url, server.variant.agent_id)


def write_spec(text: str) -> dict:
    """The script of a turn that writes the spec artifact and completes."""
    return {"outputs": [{"kind": "artifact", "id": SPEC, "text": text}, COMPLETED]}


def version_of(stored) -> str:
    """The version the server numbered the task's one artifact with."""
    [written] = stored.artifacts
    return MessageToDict(written.metadata)["version"]


@pytest.mark.variant("evolution")
async def test_artifact_versions_continue_across_a_restart(
    pg_server: ServeProcess,
    pg_client: ScenarioClient,
) -> None:
    """The first task of a context numbers its artifact 0, the first task after a restart 1, and finds the first."""
    context_id = f"restart-versions-{uuid.uuid4().hex}"

    first = await run_script(pg_client, context_id=context_id, **write_spec("plan one"))

    pg_server.restart()
    async with await reconnect(pg_server) as fresh_client:
        second = await run_script(fresh_client, context_id=context_id, find_artifact=SPEC, **write_spec("plan two"))

    assert (version_of(first), version_of(second)) == ("0", "1")
    [found] = [data for data in data_parts(second) if "found" in data]
    assert found == {"found": True, "task_id": first.id, "version": 0, "text": "plan one"}


@pytest.mark.variant("evolution")
async def test_a_shutdown_stops_a_running_turn_and_closes_its_task_with_server_shutdown(
    pg_server: ServeProcess,
    pg_client: ScenarioClient,
) -> None:
    """The open stream receives the outcome; the turn stopped, so the task is closed rather than settled."""
    task_id, stream = await start_working(pg_client)

    restarting = asyncio.create_task(asyncio.to_thread(pg_server.restart))
    events = await rest_of(stream)
    await restarting

    closing = final_task(events)
    assert (closing.task_id, closing.state) == (task_id, "FAILED")
    async with await reconnect(pg_server) as fresh_client:
        stored = await fresh_client.get_task(task_id)
    assert stored.status.state == TaskState.TASK_STATE_FAILED
    assert SETTLED_REASON_KEY not in stored.metadata
    assert SHUTDOWN_ERROR in data_parts(stored)


@pytest.mark.variant("evolution")
async def test_a_turn_that_does_not_stop_in_time_is_settled_by_the_shutdown(
    pg_server: ServeProcess,
    pg_client: ScenarioClient,
) -> None:
    """The window runs out; the drain settles the task as ``FAILED`` with ``server_shutdown``."""
    task_id, stream = await start_working(pg_client, hang_on_cancel=True)
    with contextlib.suppress(Exception):
        await stream.aclose()

    await asyncio.to_thread(pg_server.restart)

    async with await reconnect(pg_server) as fresh_client:
        stored = await fresh_client.get_task(task_id)
    assert stored.status.state == TaskState.TASK_STATE_FAILED
    assert MessageToDict(stored.metadata)[SETTLED_REASON_KEY] == "server_shutdown"


@pytest.mark.variant("evolution")
@pytest.mark.command("slow")
async def test_an_open_stream_does_not_hold_the_shutdown(
    pg_server: ServeProcess,
    pg_client: ScenarioClient,
) -> None:
    """A stream on a turn that would run for minutes ends within the shutdown's bounded wait."""
    stream = pg_client.stream("slow 120")
    async for response in stream:
        event = to_event(_payload_of(response))
        if event.state == "WORKING" and event.text:
            break

    started = time.monotonic()
    restarting = asyncio.create_task(asyncio.to_thread(pg_server.restart))
    await rest_of(stream)
    ended_after = time.monotonic() - started
    await restarting

    assert ended_after < 20, f"the open stream held the shutdown for {ended_after:.0f}s"


@pytest.mark.variant("evolution")
async def test_a_small_shutdown_budget_gives_the_turns_a_shorter_window_and_says_so_in_cancel(
    small_budget_server: ServeProcess,
) -> None:
    """A turn that stops is told the window in ``cancel()``; one that does not is settled once the window is over."""
    async with await reconnect(small_budget_server) as client:
        stopping, stopping_stream = await start_working(client)
        hanging, hanging_stream = await start_working(client, hang_on_cancel=True)
        for stream in (stopping_stream, hanging_stream):
            with contextlib.suppress(Exception):
                await stream.aclose()

    await asyncio.to_thread(small_budget_server.restart)

    async with await reconnect(small_budget_server) as fresh_client:
        stopped = await fresh_client.get_task(stopping)
        settled = await fresh_client.get_task(hanging)
    assert stopped.status.state == TaskState.TASK_STATE_FAILED
    assert SHUTDOWN_ERROR in data_parts(stopped)
    assert MessageToDict(stopped.metadata)[URI]["stopped_within"] == SMALL_BUDGET_TURN_WINDOW
    assert settled.status.state == TaskState.TASK_STATE_FAILED
    assert MessageToDict(settled.metadata)[SETTLED_REASON_KEY] == "server_shutdown"
    logged = small_budget_server.log_path.read_text(encoding="utf-8", errors="replace")
    assert f"did not end within {SMALL_BUDGET_TURN_WINDOW:.1f}s of the shutdown" in logged

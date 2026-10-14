"""What a toolkit's task keeps across a server restart, on a real database.

The toolkit runs in the server process, so a restart replaces it with a new
instance that knows nothing the old one did. Everything a toolkit relies on
between turns therefore has to come from the store: its object in
``task.metadata[<uri>]``, which a resumed turn is handed as
``ToolkitContext.task_metadata``, and the artifact versions of its context,
which the next task numbers from and ``find_artifact`` reads back.

What the toolkit was handed is read from the report a turn of the scripted
toolkit makes, since the server is another process.

An orderly shutdown asks every running turn to stop first: the toolkit's own
``failed`` result coded ``server_shutdown`` closes the task and the stream a
client has open on it. A turn that does not stop within the grace period is
settled by the server instead, and a stream open on any task does not hold
the shutdown past its bounded wait.
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

from tests.scenarios.harness import ScenarioClient, ServeProcess, ServeVariant, final_task
from tests.scenarios.harness.pg import postgres_env
from tests.scenarios.harness.recorder import _payload_of, to_event
from tests.scenarios.toolkits import COMPLETED, TOOLKIT, data_parts, reports, rest_of, run_script, start_working
from tests.support.scripted_toolkit.contract import URI

pytestmark = [pytest.mark.persistence]

SPEC = "evolution-spec"

SETTLED_REASON_KEY = "aion:settledReason"
"""Task metadata saying the server, not the toolkit, decided the final state."""

SHUTDOWN_ERROR = {"code": "server_shutdown", "message": "The server stopped during the turn.", "retryable": True}
"""The error the scripted toolkit closes a turn with when the server stops."""


@pytest.fixture
def pg_variant() -> ServeVariant:
    """The toolkit deployment with a Postgres backend."""
    return dataclasses.replace(TOOLKIT, env={**TOOLKIT.env, **postgres_env()})


async def reconnect(server: ServeProcess) -> ScenarioClient:
    """A client on the server that is running now, whichever process that is."""
    return await ScenarioClient.connect(server.base_url, server.variant.agent_id)


def write_spec(text: str) -> dict:
    """The script of a turn that writes the spec artifact and completes."""
    return {
        "outputs": [
            {
                "kind": "artifact",
                "artifact": {"id": SPEC, "parts": [{"kind": "text", "text": text}]},
                "last_chunk": True,
            },
            COMPLETED,
        ]
    }


def version_of(stored) -> str:
    """The version the server numbered the task's one artifact with."""
    [written] = stored.artifacts
    return MessageToDict(written.metadata)["version"]


@pytest.mark.variant("toolkit")
async def test_the_object_of_a_waiting_task_survives_a_restart_and_is_handed_back_on_resume(
    pg_server: ServeProcess,
    pg_client: ScenarioClient,
) -> None:
    """A turn that waits for input, a restart, and the answer: the new toolkit instance gets the object back."""
    stored_object = {"stage": "waiting", "plan": {"done": 1, "total": 2}}
    waiting = await run_script(
        pg_client,
        outputs=[
            {"kind": "message", "message": {"parts": [{"kind": "text", "text": "Which one?"}]}},
            {"kind": "result", "state": "input-required", "task_metadata": stored_object},
        ],
    )
    assert waiting.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    assert MessageToDict(waiting.metadata)[URI] == stored_object

    pg_server.restart()
    async with await reconnect(pg_server) as fresh_client:
        kept = await fresh_client.get_task(waiting.id)
        resumed = await run_script(
            fresh_client,
            task_id=waiting.id,
            context_id=waiting.context_id,
            report=True,
            outputs=[{"kind": "result", "state": "completed", "task_metadata": {"stage": "finished"}}],
        )

    assert MessageToDict(kept.metadata)[URI] == stored_object
    [report] = reports(resumed)
    assert (report["kind"], report["task_id"]) == ("resume", waiting.id)
    assert report["task_metadata"] == stored_object
    assert resumed.status.state == TaskState.TASK_STATE_COMPLETED
    assert MessageToDict(resumed.metadata)[URI] == {"stage": "finished", "plan": {"done": 1, "total": 2}}


@pytest.mark.variant("toolkit")
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


@pytest.mark.variant("toolkit")
async def test_a_shutdown_closes_a_running_turn_with_the_toolkits_server_shutdown_result(
    pg_server: ServeProcess,
    pg_client: ScenarioClient,
) -> None:
    """The open stream receives the outcome, and the stored task is the toolkit's, not a settlement."""
    task_id, stream = await start_working(pg_client, URI)

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


@pytest.mark.variant("toolkit")
async def test_a_turn_that_does_not_stop_in_time_is_settled_by_the_shutdown(
    pg_server: ServeProcess,
    pg_client: ScenarioClient,
) -> None:
    """The grace period runs out; the server settles the task as ``FAILED`` with ``server_shutdown``."""
    task_id, stream = await start_working(pg_client, URI, hang_on_cancel=True)
    with contextlib.suppress(Exception):
        await stream.aclose()

    await asyncio.to_thread(pg_server.restart)

    async with await reconnect(pg_server) as fresh_client:
        stored = await fresh_client.get_task(task_id)
    assert stored.status.state == TaskState.TASK_STATE_FAILED
    assert MessageToDict(stored.metadata)[SETTLED_REASON_KEY] == "server_shutdown"


@pytest.mark.variant("toolkit")
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

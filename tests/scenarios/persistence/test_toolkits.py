"""What a toolkit's task keeps across a server restart, on a real database.

The toolkit runs in the server process, so a restart replaces it with a new
instance that knows nothing the old one did. Everything a toolkit relies on
between turns therefore has to come from the store: its object in
``task.metadata[<uri>]``, which a resumed turn is handed as
``ToolkitContext.task_metadata``, and the artifact versions of its context,
which the next task numbers from and ``find_artifact`` reads back.

What the toolkit was handed is read from the report a turn of the scripted
toolkit makes, since the server is another process.
"""

from __future__ import annotations

import dataclasses
import uuid

import pytest
from a2a.types import TaskState
from google.protobuf.json_format import MessageToDict

from tests.scenarios.harness import ScenarioClient, ServeProcess, ServeVariant
from tests.scenarios.harness.pg import postgres_env
from tests.scenarios.toolkits import COMPLETED, TOOLKIT, data_parts, reports, run_script
from tests.support.scripted_toolkit.contract import URI

pytestmark = [pytest.mark.persistence]

SPEC = "evolution-spec"


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

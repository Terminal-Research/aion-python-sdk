"""A toolkit plugin served by a real ``aion serve``: one turn on the wire, and one refusal.

The deployment has the scripted toolkit installed - its directory is on the
server's ``PYTHONPATH``, so its ``aion.toolkits`` entry point is found the
way an installed package's is - and its extension in ``enabled_extensions``.
Each request carries the script of its turn in a data part, as an event of
the toolkit's own vocabulary.

What a turn becomes is the toolkit host's translation, checked here once
through the server: the stream opens and closes with the Task, the toolkit's
object lands whole in ``task.metadata`` under its URI, the status line
survives updates that only change that object, and every message is an
Event of the turn. A request the toolkit refuses before its task exists is a
JSON-RPC error carrying the refusal's code, and leaves no task.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, AsyncIterator

import httpx
import pytest
import pytest_asyncio
from google.protobuf.json_format import MessageToDict

from tests.scenarios.harness import (
    EVENT_EXTENSION_URI,
    ScenarioClient,
    ServeProcess,
    ServeVariant,
    artifact,
    assert_shape,
    chunks,
    event_envelope,
    final_task,
    status,
    task,
)
from tests.support.scripted_toolkit.contract import SCRIPT_EVENT_TYPE, SCRIPT_SCHEMA, SITE, URI

pytestmark = pytest.mark.toolkits

TOOLKIT = ServeVariant(
    name="toolkit",
    enabled_extensions=(URI,),
    env={"PYTHONPATH": os.pathsep.join(filter(None, [str(SITE), os.environ.get("PYTHONPATH")]))},
)

INVALID_PARAMS = -32602


@pytest.fixture(scope="session")
def toolkit_server(server_for) -> ServeProcess:
    """A deployment with the scripted toolkit installed and enabled."""
    return server_for(TOOLKIT)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def toolkit_client(toolkit_server) -> AsyncIterator[ScenarioClient]:
    """A client on the toolkit deployment."""
    scenario_client = await ScenarioClient.connect(toolkit_server.base_url, toolkit_server.variant.agent_id)
    try:
        yield scenario_client
    finally:
        await scenario_client.close()


def script(**fields: Any):
    """The event that carries a turn's script."""
    return event_envelope(kind=SCRIPT_EVENT_TYPE, extension_uri=URI, schema=SCRIPT_SCHEMA, payload=fields)


TURN = [
    {
        "kind": "message",
        "message": {"type": "stage.changed", "parts": [{"kind": "text", "text": "Preparing"}]},
        "task_metadata": {"stage": "preparing", "plan": {"done": 0, "total": 2}},
    },
    {"kind": "message", "message": {"parts": [{"kind": "text", "text": "Work"}]}, "partial": True},
    {"kind": "message", "message": {"parts": [{"kind": "text", "text": "ing"}]}, "partial": True},
    {"kind": "status", "task_metadata": {"plan": {"done": 1, "total": 2}}},
    {
        "kind": "artifact",
        "artifact": {"id": "report", "name": "Report", "parts": [{"kind": "text", "text": "All done"}]},
        "last_chunk": True,
    },
    {
        "kind": "result",
        "state": "completed",
        "message": {"parts": [{"kind": "text", "text": "Finished"}]},
        "task_metadata": {"stage": "finished", "plan": {"done": 2, "total": 2}},
    },
]


@pytest.mark.variant("toolkit")
async def test_a_toolkit_turn_streams_and_completes_with_its_object(toolkit_client: ScenarioClient) -> None:
    """Messages, deltas, details and an artifact on the stream; the object whole in the task."""
    envelope = script(outputs=TURN)

    events = await toolkit_client.send(
        "run", extensions=[URI], data=[envelope.data], message_metadata=envelope.message_metadata
    )

    assert_shape(events, [
        task("SUBMITTED"),
        status("WORKING", text=""),
        status("WORKING", text="Preparing"),
        chunks(2),
        status("WORKING", text=""),
        artifact(name="Report", last=True),
        task("COMPLETED", final=True),
    ])
    closing = final_task(events)
    expected = {"stage": "finished", "plan": {"done": 2, "total": 2}}
    assert closing.metadata[URI] == expected
    assert closing.text == "Finished"

    stored = await toolkit_client.get_task(closing.task_id)
    assert _plain(stored.metadata)[URI] == expected
    assert [item.artifact_id for item in stored.artifacts] == ["report"]
    preparing = next(message for message in stored.history if _text(message) == "Preparing")
    markup = _plain(preparing.metadata)[EVENT_EXTENSION_URI]
    assert (markup["type"], markup["source"], markup["id"]) == ("stage.changed", URI, preparing.message_id)


@pytest.mark.variant("toolkit")
async def test_a_status_update_keeps_the_status_line(toolkit_client: ScenarioClient) -> None:
    """Details without a message change the object, not what the task last said."""
    envelope = script(
        outputs=[
            {"kind": "message", "message": {"parts": [{"kind": "text", "text": "Halfway"}]}},
            {"kind": "status", "task_metadata": {"usage": {"tokens": 10}}},
            {"kind": "result", "state": "input-required"},
        ]
    )

    events = await toolkit_client.send(
        "run", extensions=[URI], data=[envelope.data], message_metadata=envelope.message_metadata
    )

    closing = final_task(events)
    assert closing.state == "INPUT_REQUIRED"
    assert closing.text == "Halfway"
    assert closing.metadata[URI] == {"usage": {"tokens": 10}}


@pytest.mark.variant("toolkit")
async def test_a_refused_request_is_an_error_with_its_code_and_leaves_no_task(
    toolkit_client: ScenarioClient,
) -> None:
    """The toolkit's refusal is the JSON-RPC error, with its code; no task was opened for it."""
    context_id = f"refused-{uuid.uuid4().hex}"
    envelope = script(refuse={"code": "invalid_directive", "message": "No target.", "retryable": False})

    response = await toolkit_client.rpc(
        "SendMessage",
        {
            "message": {
                "messageId": uuid.uuid4().hex,
                "contextId": context_id,
                "role": "ROLE_USER",
                "extensions": [URI],
                "metadata": dict(envelope.message_metadata),
                "parts": [
                    {"text": "run"},
                    {"data": envelope.data.data, "metadata": dict(envelope.data.metadata)},
                ],
            }
        },
    )

    error = response["error"]
    assert error["code"] == INVALID_PARAMS
    assert error["message"] == "No target."
    assert error["data"][0]["metadata"] == {"code": "invalid_directive", "retryable": "false"}
    assert await toolkit_client.tasks_in_context(context_id) == []


@pytest.mark.variant("toolkit")
def test_the_card_offers_the_enabled_toolkit(toolkit_server: ServeProcess) -> None:
    """An installed toolkit the agent enables is an extension the card announces."""
    card = httpx.get(f"{toolkit_server.agent_url()}/.well-known/agent-card.json", timeout=10).json()

    assert URI in [extension["uri"] for extension in card["capabilities"]["extensions"]]


def _plain(struct) -> dict:
    return MessageToDict(struct)


def _text(message) -> str:
    return "".join(part.text for part in message.parts if part.text)

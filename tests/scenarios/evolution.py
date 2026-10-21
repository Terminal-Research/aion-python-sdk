"""The evolution extension as a scenario drives it: its deployment, its scripts and its report.

Read by the evolution scenarios of ``core/``, ``persistence/`` and
``distributed/``. The deployment enables the evolution extension - the SDK's
wrapper - with the stand-in toolkit of ``tests.support.scripted_executor``
installed: its directory is on the server's ``PYTHONPATH``. The directive
carries the turn's script, and the daemon payload the extension requires
rides on every request.

The server runs in another process, so the toolkit's own record of the calls
it received is out of reach; a script that asks for a ``report`` has the
toolkit say it in a message, and ``reported`` reads it.
"""

from __future__ import annotations

import contextlib
import os
import uuid
from typing import Any, AsyncIterator, Optional

from google.protobuf.json_format import MessageToDict

from tests.scenarios.harness import ScenarioClient, ServeVariant, event_envelope, final_task
from tests.scenarios.harness.daemon import DAEMON_EXTENSION_URI, daemon_metadata
from tests.scenarios.harness.recorder import Ev, _payload_of, to_event
from tests.support.scripted_executor.contract import (
    EVOLUTION_DIRECTIVE_EVENT_TYPE,
    EVOLUTION_DIRECTIVE_SCHEMA,
    EVOLUTION_URI,
    FAKE_TOOLKIT,
)

__all__ = [
    "COMPLETED",
    "EVOLUTION",
    "EXTENSIONS",
    "URI",
    "WORKING",
    "data_parts",
    "reported",
    "rejection",
    "reports",
    "rest_of",
    "rpc_script",
    "run_script",
    "script",
    "start_working",
]

URI = EVOLUTION_URI
"""The evolution extension."""

EXTENSIONS = [URI, DAEMON_EXTENSION_URI]
"""What every evolution request activates: the extension and the daemon extension it requires."""

OPTIONS = {"callers": ["anonymous_session"]}
"""The extension's options: the scenarios call with anonymous session tokens."""

EVOLUTION_ENV = {
    "PYTHONPATH": os.pathsep.join(filter(None, [str(FAKE_TOOLKIT), os.environ.get("PYTHONPATH")])),
    "EVOLUTION_MODEL_PROVIDER": "local_session",
}
"""The environment that installs the stand-in toolkit - its directory on the server's ``PYTHONPATH`` - and
names a provider that needs nothing of the deployment: no model is called."""

EVOLUTION = ServeVariant(
    name="evolution", enabled_extensions=(DAEMON_EXTENSION_URI, {URI: OPTIONS}), env=EVOLUTION_ENV
)
"""A deployment with the evolution extension enabled and the stand-in toolkit installed."""

COMPLETED = {"kind": "status", "state": "completed"}
"""The output that ends a turn as completed."""

WORKING = {"kind": "status", "text": "Working"}
"""A status update that says the turn is working; a scenario waits for it before acting on the turn."""


def script(**fields: Any):
    """The directive event that carries a turn's script."""
    return event_envelope(
        kind=EVOLUTION_DIRECTIVE_EVENT_TYPE, extension_uri=URI, schema=EVOLUTION_DIRECTIVE_SCHEMA, payload=fields
    )


async def run_script(client: ScenarioClient, **fields: Any):
    """Send one message carrying a script, as a new task or as ``task_id``/``context_id`` continue one.

    ``task_id`` and ``context_id`` are taken out of ``fields``; the rest is the script.

    Returns:
        The stored task the turn ended with.
    """
    ids = {name: fields.pop(name) for name in ("task_id", "context_id") if name in fields}
    envelope = script(**fields)
    events = await client.send(
        "run",
        extensions=EXTENSIONS,
        metadata=daemon_metadata(),
        data=[envelope.data],
        message_metadata=envelope.message_metadata,
        **ids,
    )
    return await client.get_task(final_task(events).task_id)


async def rpc_script(
    client: ScenarioClient,
    *,
    context_id: Optional[str] = None,
    task_id: Optional[str] = None,
    **fields: Any,
) -> dict:
    """Send a script with ``SendMessage`` by hand, so a refusal can be read as it was answered.

    Returns:
        The decoded JSON-RPC response.
    """
    envelope = script(**fields)
    message: dict[str, Any] = {
        "messageId": uuid.uuid4().hex,
        "role": "ROLE_USER",
        "extensions": EXTENSIONS,
        "metadata": dict(envelope.message_metadata),
        "parts": [{"text": "run"}, {"data": envelope.data.data, "metadata": dict(envelope.data.metadata)}],
    }
    if context_id is not None:
        message["contextId"] = context_id
    if task_id is not None:
        message["taskId"] = task_id
    return await client.rpc("SendMessage", {"message": message, "metadata": daemon_metadata()})


async def start_working(
    client: ScenarioClient, *, context_id: Optional[str] = None, **fields: Any
) -> tuple[str, AsyncIterator]:
    """Start a turn that says it is working and then waits for ``cancel()``.

    ``fields`` add to that script. Returns the task id once the turn has said
    it is working, and the stream, left open for the caller to read on or
    close; closing it does not stop the turn.
    """
    envelope = script(outputs=[WORKING], wait_for_cancel=True, **fields)
    stream = client.stream(
        "run",
        metadata=daemon_metadata(),
        extensions=EXTENSIONS,
        data=[envelope.data],
        message_metadata=envelope.message_metadata,
        **({"context_id": context_id} if context_id else {}),
    )
    task_id: Optional[str] = None
    async for response in stream:
        event = to_event(_payload_of(response))
        task_id = task_id or event.task_id
        if event.state == "WORKING" and event.text == "Working" and task_id:
            return task_id, stream
    raise AssertionError("the turn ended before it said it was working")


async def rest_of(stream) -> list[Ev]:
    """Every event a stream still delivers; a stream the server cuts off ends the list."""
    events: list[Ev] = []
    with contextlib.suppress(Exception):
        async for response in stream:
            events.append(to_event(_payload_of(response)))
    return events


def rejection(response: dict) -> dict:
    """The error a ``SendMessage`` answer's task was rejected with: ``{"code", "message", "retryable"}``."""
    assert "result" in response, f"the request was refused before a task existed: {response}"
    task = response["result"]["task"]
    assert task["status"]["state"] == "TASK_STATE_REJECTED", task["status"]
    return task["status"]["message"]["parts"][-1]["data"]


def data_parts(stored) -> list[dict]:
    """The data parts of a stored task's messages, in order: its history, then its status line."""
    messages = list(stored.history)
    if stored.status.HasField("message"):
        messages.append(stored.status.message)
    return [
        MessageToDict(part.data)
        for message in messages
        for part in message.parts
        if part.WhichOneof("content") == "data"
    ]


def reports(stored) -> list[dict]:
    """The reports the toolkit made in the turns of a stored task, in order."""
    return [data for data in data_parts(stored) if "cancels" in data]


async def reported(client: ScenarioClient) -> dict:
    """What the toolkit says it has been handed so far, asked in a turn of its own."""
    [report] = reports(await run_script(client, report=True, outputs=[COMPLETED]))
    return report

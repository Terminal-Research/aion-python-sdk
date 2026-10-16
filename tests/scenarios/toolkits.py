"""The scripted toolkit as a scenario drives it: its deployment, its scripts and its report.

Read by the toolkit scenarios of ``core/``, ``persistence/`` and
``distributed/``. The server runs in another process, so the toolkit's own
record of the calls it received is out of reach; a script that asks for a
``report`` has the toolkit say it in a message, and ``reported`` reads it.

The deployment installs the toolkit twice: under ``URI`` as it is, and under
``EXCLUSIVE_URI`` as a toolkit that runs one task per context. Every helper
takes the URI of the one it addresses.
"""

from __future__ import annotations

import contextlib
import os
import uuid
from typing import Any, AsyncIterator, Optional

from google.protobuf.json_format import MessageToDict

from tests.scenarios.harness import ScenarioClient, ServeVariant, event_envelope, final_task
from tests.scenarios.harness.recorder import Ev, _payload_of, to_event
from tests.support.scripted_toolkit.contract import EXCLUSIVE_URI, SITE, URI, script_contract

__all__ = [
    "COMPLETED",
    "EXCLUSIVE_URI",
    "TOOLKIT",
    "WORKING",
    "data_parts",
    "reported",
    "reports",
    "rest_of",
    "rpc_script",
    "run_script",
    "script",
    "start_working",
]

TOOLKIT_ENV = {"PYTHONPATH": os.pathsep.join(filter(None, [str(SITE), os.environ.get("PYTHONPATH")]))}
"""The environment that installs both toolkits: their directory on the server's ``PYTHONPATH``."""

TOOLKIT = ServeVariant(name="toolkit", enabled_extensions=(URI, EXCLUSIVE_URI), env=TOOLKIT_ENV)
"""A deployment with the scripted toolkits installed and enabled."""

COMPLETED = {"kind": "result", "state": "completed"}
"""The output that ends a turn as completed."""

WORKING = {"kind": "message", "message": {"parts": [{"kind": "text", "text": "Working"}]}}
"""A message that says the turn is working; a scenario waits for it before acting on the turn."""


def script(uri: str = URI, **fields: Any):
    """The event that carries a turn's script for the toolkit serving ``uri``."""
    kind, schema = script_contract(uri)
    return event_envelope(kind=kind, extension_uri=uri, schema=schema, payload=fields)


async def run_script(client: ScenarioClient, uri: str = URI, **fields: Any):
    """Send one message carrying a script, as a new task or as ``task_id``/``context_id`` continue one.

    ``task_id`` and ``context_id`` are taken out of ``fields``; the rest is the script.

    Returns:
        The stored task the turn ended with.
    """
    ids = {name: fields.pop(name) for name in ("task_id", "context_id") if name in fields}
    envelope = script(uri, **fields)
    events = await client.send(
        "run", extensions=[uri], data=[envelope.data], message_metadata=envelope.message_metadata, **ids
    )
    return await client.get_task(final_task(events).task_id)


async def rpc_script(
    client: ScenarioClient,
    uri: str = URI,
    *,
    context_id: Optional[str] = None,
    task_id: Optional[str] = None,
    **fields: Any,
) -> dict:
    """Send a script with ``SendMessage`` by hand, so a refusal's ``error.data`` can be read.

    Returns:
        The decoded JSON-RPC response.
    """
    envelope = script(uri, **fields)
    message: dict[str, Any] = {
        "messageId": uuid.uuid4().hex,
        "role": "ROLE_USER",
        "extensions": [uri],
        "metadata": dict(envelope.message_metadata),
        "parts": [{"text": "run"}, {"data": envelope.data.data, "metadata": dict(envelope.data.metadata)}],
    }
    if context_id is not None:
        message["contextId"] = context_id
    if task_id is not None:
        message["taskId"] = task_id
    return await client.rpc("SendMessage", {"message": message})


async def start_working(
    client: ScenarioClient, uri: str = URI, *, context_id: Optional[str] = None, **fields: Any
) -> tuple[str, AsyncIterator]:
    """Start a turn that says it is working and then waits for ``cancel()``.

    ``fields`` add to that script. Returns the task id once the turn has said
    it is working, and the stream, left open for the caller to read on or
    close; closing it does not stop the turn.
    """
    envelope = script(uri, outputs=[WORKING], wait_for_cancel=True, **fields)
    stream = client.stream(
        "run",
        extensions=[uri],
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


async def reported(client: ScenarioClient, uri: str = URI) -> dict:
    """What the toolkit serving ``uri`` says it has been handed so far, asked in a turn of its own."""
    [report] = reports(await run_script(client, uri, report=True, outputs=[COMPLETED]))
    return report

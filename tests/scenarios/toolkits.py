"""The scripted toolkit as a scenario drives it: its deployment, its scripts and its report.

Read by the toolkit scenarios of ``core/`` and ``persistence/``. The server
runs in another process, so the toolkit's own record of the calls it received
is out of reach; a script that asks for a ``report`` has the toolkit say it in
a message, and ``reported`` reads it.
"""

from __future__ import annotations

import os
from typing import Any

from google.protobuf.json_format import MessageToDict

from tests.scenarios.harness import ScenarioClient, ServeVariant, event_envelope, final_task
from tests.support.scripted_toolkit.contract import SCRIPT_EVENT_TYPE, SCRIPT_SCHEMA, SITE, URI

__all__ = ["COMPLETED", "TOOLKIT", "data_parts", "reported", "reports", "run_script", "script"]

TOOLKIT = ServeVariant(
    name="toolkit",
    enabled_extensions=(URI,),
    env={"PYTHONPATH": os.pathsep.join(filter(None, [str(SITE), os.environ.get("PYTHONPATH")]))},
)
"""A deployment with the scripted toolkit installed - its directory is on the server's ``PYTHONPATH`` - and enabled."""

COMPLETED = {"kind": "result", "state": "completed"}
"""The output that ends a turn as completed."""


def script(**fields: Any):
    """The event that carries a turn's script."""
    return event_envelope(kind=SCRIPT_EVENT_TYPE, extension_uri=URI, schema=SCRIPT_SCHEMA, payload=fields)


async def run_script(client: ScenarioClient, **fields: Any):
    """Send one message carrying a script, as a new task or as ``task_id``/``context_id`` continue one.

    ``task_id`` and ``context_id`` are taken out of ``fields``; the rest is the script.

    Returns:
        The stored task the turn ended with.
    """
    ids = {name: fields.pop(name) for name in ("task_id", "context_id") if name in fields}
    envelope = script(**fields)
    events = await client.send(
        "run", extensions=[URI], data=[envelope.data], message_metadata=envelope.message_metadata, **ids
    )
    return await client.get_task(final_task(events).task_id)


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

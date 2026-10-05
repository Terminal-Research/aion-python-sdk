"""The Context extension on a real deployment: discover it, list and read contexts, delete one.

A direct client - the kind that talks to ``aion serve`` without the Aion
control plane - finds the extension on the agent card and uses its three
methods over JSON-RPC and over HTTP+JSON through the proxy:

* a conversation the caller had is listed and read back, its turns in order;
* another caller neither sees it nor can delete it;
* deleting it cancels the turn still running in it, and afterwards nothing of
  it is listed, read or continued: the same context ID starts a conversation
  with no memory of the old one.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from tests.scenarios.commands import echo_text
from tests.scenarios.harness import ScenarioClient, ServeProcess, final_task, session_subject
from tests.scenarios.harness.recorder import _payload_of, to_event

pytestmark = [pytest.mark.contexts]

CONTEXT_EXTENSION_URI = "https://docs.aion.to/a2a/extensions/aion/context/1.0.0"
"""The unified Context extension, written out: the harness imports nothing from ``aion``."""

CONTEXT_NOT_FOUND = 1000
TASK_NOT_FOUND = -32001


async def _other_caller(server: ServeProcess) -> ScenarioClient:
    return await ScenarioClient.connect(
        server.base_url, server.variant.agent_id, subject=session_subject(str(uuid.uuid4()))
    )


async def _context_ids(client: ScenarioClient) -> list[str]:
    response = await client.rpc("GetContexts", {"historyLength": 100})
    return [entry["contextId"] for entry in response["result"]]


async def test_the_card_declares_the_context_extension(client: ScenarioClient) -> None:
    """Declared once and optional: its methods need no activation."""
    declared = [ext for ext in client.card.capabilities.extensions if ext.uri == CONTEXT_EXTENSION_URI]

    assert len(declared) == 1
    assert not declared[0].required


@pytest.mark.command("echo")
async def test_a_conversation_is_listed_and_read_back(server: ServeProcess) -> None:
    """A caller's conversation is listed and read back in order, over JSON-RPC and HTTP+JSON."""
    context_id = str(uuid.uuid4())
    async with await _other_caller(server) as client:
        final_task(await client.send("echo first", context_id=context_id))
        final_task(await client.send("echo second", context_id=context_id))

        assert context_id in await _context_ids(client)
        view = (await client.rpc("GetContext", {"contextId": context_id}))["result"]
        texts = [part["text"] for message in view["history"] for part in message["parts"] if "text" in part]
        assert texts == ["echo first", echo_text("first"), "echo second", echo_text("second")]
        assert view["status"]["state"] == "TASK_STATE_COMPLETED"
        assert view["lastActivityAt"].endswith("Z")

        newest = (await client.rpc("GetContext", {"contextId": context_id, "historyLength": 2}))["result"]
        assert newest["history"] == view["history"][-2:]

        # The same answers over HTTP+JSON, through the proxy.
        over_http = await client._http.post(f"{client.agent_url}/context:get", json={"contextId": context_id})
        assert over_http.status_code == 200
        assert over_http.json()["history"] == view["history"]


@pytest.mark.command("echo")
async def test_another_caller_neither_sees_nor_deletes_it(server: ServeProcess) -> None:
    """Knowing a context ID lets another caller neither read nor delete the context."""
    context_id = str(uuid.uuid4())
    async with await _other_caller(server) as owner, await _other_caller(server) as stranger:
        final_task(await owner.send("echo mine", context_id=context_id))

        assert context_id not in await _context_ids(stranger)
        read = await stranger.rpc("GetContext", {"contextId": context_id})
        assert read["error"]["code"] == CONTEXT_NOT_FOUND
        assert (await stranger.rpc("DeleteContext", {"contextId": context_id}))["result"] == {
            "contextId": context_id
        }

        assert context_id in await _context_ids(owner)


@pytest.mark.command("slow")
async def test_deleting_a_context_cancels_its_running_turn_and_forgets_it(server: ServeProcess) -> None:
    """Deleting a context cancels its running turn; the ID then starts a conversation with no memory."""
    context_id = str(uuid.uuid4())
    async with await _other_caller(server) as client:
        final_task(await client.send("echo before", context_id=context_id))

        task_id = None
        stream = client.stream("slow 30", context_id=context_id)
        async for response in stream:
            event = to_event(_payload_of(response))
            task_id = task_id or event.task_id
            if event.state == "WORKING" and task_id:
                break
        assert task_id is not None

        deleted = await asyncio.wait_for(client.rpc("DeleteContext", {"contextId": context_id}), timeout=60)
        assert deleted["result"] == {"contextId": context_id}, deleted
        # The running turn's own stream ends: it was cancelled, not left behind.
        rest = [to_event(_payload_of(response)) async for response in stream]
        assert rest and rest[-1].state == "CANCELED"

        assert context_id not in await _context_ids(client)
        assert (await client.rpc("GetContext", {"contextId": context_id}))["error"]["code"] == CONTEXT_NOT_FOUND
        assert (await client.rpc("GetTask", {"id": task_id}))["error"]["code"] == TASK_NOT_FOUND

        # The ID is free again, and what follows remembers nothing before it.
        final_task(await client.send("echo after", context_id=context_id))
        view = (await client.rpc("GetContext", {"contextId": context_id}))["result"]
        texts = [part["text"] for message in view["history"] for part in message["parts"] if "text" in part]
        assert texts == ["echo after", echo_text("after")]

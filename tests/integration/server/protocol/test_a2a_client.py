"""a2a-sdk's own client, built from the agent card, works against the server."""

from __future__ import annotations

import uuid

import httpx
import pytest
from a2a.client import ClientConfig, ClientFactory
from a2a.client.card_resolver import parse_agent_card
from a2a.types.a2a_pb2 import GetTaskRequest, Message, Part, Role, SendMessageRequest, TaskState

from tests.support.agent_app import user_token

pytestmark = [pytest.mark.asyncio(loop_scope="module")]


async def _client(served, *, streaming: bool):
    """A client built from the card the server serves, its HTTP going to the application over ASGI."""
    card = parse_agent_card((await served.client.get("/.well-known/agent-card.json")).json())
    http = httpx.AsyncClient(
        transport=served.client._transport, headers={"Authorization": f"Bearer {user_token()}"}
    )
    return ClientFactory(ClientConfig(streaming=streaming, httpx_client=http)).create(card), http


def _request(text: str) -> SendMessageRequest:
    return SendMessageRequest(
        message=Message(message_id=uuid.uuid4().hex, role=Role.ROLE_USER, parts=[Part(text=text)])
    )


@pytest.mark.parametrize("streaming", [False, True], ids=["send", "stream"])
async def test_the_client_sends_a_message_and_reads_the_task_back(served, streaming) -> None:
    client, http = await _client(served, streaming=streaming)
    try:
        events = [event async for event in client.send_message(_request("hello"))]
        task = events[-1].task
        stored = await client.get_task(GetTaskRequest(id=task.id))
    finally:
        await http.aclose()

    assert events[0].WhichOneof("payload") == "task"
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    assert [part.text for part in task.status.message.parts] == ["echo: hello"]
    assert stored.status.state == TaskState.TASK_STATE_COMPLETED

"""The application ``aion serve`` runs answers both protocol versions its card names."""

from __future__ import annotations

import pytest

from tests.support.agent_app import AGENT_PORT

pytestmark = [pytest.mark.asyncio(loop_scope="module")]


async def test_the_card_names_a_json_rpc_interface_for_a2a_1_0_and_0_3(served) -> None:
    response = await served.client.get("/.well-known/agent-card.json")

    assert response.status_code == 200
    interfaces = response.json()["supportedInterfaces"]
    assert [(i["protocolBinding"], i["protocolVersion"]) for i in interfaces] == [
        ("JSONRPC", "1.0"),
        ("JSONRPC", "0.3"),
    ]
    assert {i["url"] for i in interfaces} == {f"http://0.0.0.0:{AGENT_PORT}"}


async def test_send_message_completes_a_task(served) -> None:
    response = await served.send("hello")

    task = response["result"]["task"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert [part["text"] for part in task["status"]["message"]["parts"]] == ["echo: hello"]


async def test_message_send_of_a2a_0_3_completes_a_task(served) -> None:
    response = await served.send_v03("hello")

    task = response["result"]
    assert task["kind"] == "task"
    assert task["status"]["state"] == "completed"
    assert [part["text"] for part in task["status"]["message"]["parts"]] == ["echo: hello"]


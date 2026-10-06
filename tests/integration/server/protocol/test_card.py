"""The agent card: cached by its ETag, and promising only what the server answers."""

from __future__ import annotations

import uuid

import pytest

from aion.core.a2a import AION_JSONRPC_METHOD_EXTENSION_BINDINGS
from aion.core.runtime import aion_a2a_extension_registry

pytestmark = [pytest.mark.asyncio(loop_scope="module")]

CARD_PATH = "/.well-known/agent-card.json"
WEBHOOK = "http://127.0.0.1:9/hook"


async def _card(served) -> dict:
    response = await served.client.get(CARD_PATH)
    assert response.status_code == 200
    return response.json()


async def test_a_matching_if_none_match_is_answered_304(served) -> None:
    first = await served.client.get(CARD_PATH)

    again = await served.client.get(CARD_PATH, headers={"If-None-Match": first.headers["etag"]})

    assert first.headers["etag"].startswith('W/"')
    assert again.status_code == 304
    assert again.content == b""


async def test_another_etag_gets_the_card(served) -> None:
    response = await served.client.get(CARD_PATH, headers={"If-None-Match": 'W/"other"'})

    assert response.status_code == 200
    assert response.json()["name"] == "Protocol agent"


async def test_a_card_that_declares_streaming_streams(served) -> None:
    assert (await _card(served))["capabilities"]["streaming"] is True

    events = await served.send("hello", method="SendStreamingMessage")

    assert events[-1]["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"


async def test_a_card_that_declares_push_notifications_takes_a_config(served) -> None:
    assert (await _card(served))["capabilities"]["pushNotifications"] is True
    task_id = (await served.send("hello"))["result"]["task"]["id"]

    created = await served.rpc("CreateTaskPushNotificationConfig", {"taskId": task_id, "url": WEBHOOK})

    assert created["result"]["url"] == WEBHOOK


async def test_every_advertised_extension_can_be_served(served) -> None:
    advertised = [extension["uri"] for extension in (await _card(served))["capabilities"]["extensions"]]

    assert advertised
    assert {uri: aion_a2a_extension_registry.unservable_reason(uri) for uri in advertised} == {
        uri: None for uri in advertised
    }


async def test_every_method_of_an_advertised_extension_answers(served) -> None:
    advertised = {extension["uri"] for extension in (await _card(served))["capabilities"]["extensions"]}
    methods = [
        method
        for method, binding in AION_JSONRPC_METHOD_EXTENSION_BINDINGS.items()
        if binding.extension_uri in advertised
    ]
    params = {"contextId": str(uuid.uuid4())}

    answers = {method: await served.rpc(method, params) for method in methods}

    assert methods
    # Each answers as the extension defines: an unknown context is its own
    # error (1000) to read and nothing to delete - never "no such method".
    assert {method: answer.get("error", {}).get("code") for method, answer in answers.items()} == {
        "GetContexts": None,
        "GetContext": 1000,
        "DeleteContext": None,
    }

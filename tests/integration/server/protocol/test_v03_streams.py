"""A2A 0.3's streams are framed and opened as the 1.0 binding's are.

Events are separated with LF only - through ``aion.proxy`` too, where CRLF
boundaries once arrived joined - and a ``message/stream`` whose declared
extensions fail verification is refused before the stream opens.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from aion.core.constants.a2a import CRON_EXTENSION_URI_V1
from tests.support.agent_app import events_of, proxied

pytestmark = [pytest.mark.asyncio(loop_scope="module")]

INVALID_PARAMS = -32602


def _v03_message(text: str) -> dict:
    return {
        "kind": "message",
        "messageId": uuid.uuid4().hex,
        "contextId": str(uuid.uuid4()),
        "role": "user",
        "parts": [{"kind": "text", "text": text}],
    }


def _call(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": 7, "method": method, "params": params}


def _assert_lf_framed(body: bytes) -> list[bytes]:
    """The events of an SSE body framed with LF alone; returns them."""
    assert b"\r" not in body
    assert body.endswith(b"\n\n")
    events = body[:-2].split(b"\n\n")
    assert all(event.startswith(b"data: ") for event in events), body
    return events


@pytest.fixture(params=["direct", "proxy"])
async def via(request, served):
    if request.param == "direct":
        yield served
    else:
        async with proxied(served) as through_proxy:
            yield through_proxy


async def test_message_stream_events_are_separated_with_lf(via) -> None:
    response = await via.post(_call("message/stream", {"message": _v03_message("hello")}), version=None)

    assert response.headers["content-type"].startswith("text/event-stream")
    events = _assert_lf_framed(response.content)
    assert len(events) > 1


async def test_tasks_resubscribe_events_are_separated_with_lf(via) -> None:
    started = events_of(
        await via.post(
            _call("message/send", {"message": _v03_message("hold"), "configuration": {"blocking": False}}),
            version=None,
        )
    )
    task_id = started["result"]["id"]

    subscription = asyncio.create_task(via.post(_call("tasks/resubscribe", {"id": task_id}), version=None))
    await asyncio.sleep(0.3)
    via.release.set()
    response = await asyncio.wait_for(subscription, timeout=10)

    assert response.headers["content-type"].startswith("text/event-stream")
    events = _assert_lf_framed(response.content)
    assert b'"completed"' in events[-1]


async def test_a_message_stream_with_an_unverifiable_extension_is_refused_before_it_opens(via) -> None:
    """The cron extension is declared and its payload is missing: ``-32602``, no stream."""
    response = await via.post(
        _call("message/stream", {"message": _v03_message("hello")}),
        version=None,
        headers={"X-A2A-Extensions": CRON_EXTENSION_URI_V1},
    )

    assert response.headers["content-type"].startswith("application/json")
    error = response.json()["error"]
    assert error["code"] == INVALID_PARAMS
    assert CRON_EXTENSION_URI_V1 in error["message"]

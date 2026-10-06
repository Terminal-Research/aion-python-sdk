"""The JSON-RPC errors a client gets for a request the server cannot serve, over the A2A 1.0 binding."""

from __future__ import annotations

import pytest

from tests.support.agent_app import events_of

pytestmark = [pytest.mark.asyncio(loop_scope="module")]

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
UNSUPPORTED_OPERATION = -32004
VERSION_NOT_SUPPORTED = -32009


async def test_a_body_that_is_not_json_is_a_parse_error(served) -> None:
    response = events_of(await served.post(None, raw=b"{not json"))

    assert response["error"]["code"] == PARSE_ERROR


@pytest.mark.parametrize(
    "body",
    [
        {"jsonrpc": "1.0", "id": 1, "method": "GetTask", "params": {"id": "t"}},
        [{"jsonrpc": "2.0", "id": 1, "method": "GetTask", "params": {"id": "t"}}],
        {"jsonrpc": "2.0", "id": 1, "params": {"id": "t"}},
    ],
    ids=["jsonrpc-1.0", "batch", "no-method"],
)
async def test_a_request_that_is_not_one_json_rpc_2_0_call_is_invalid(served, body) -> None:
    response = events_of(await served.post(body))

    assert response["error"]["code"] == INVALID_REQUEST


async def test_an_unknown_method_is_not_found(served) -> None:
    response = await served.rpc("tasks/list", {})

    assert response["error"]["code"] == METHOD_NOT_FOUND


async def test_params_that_do_not_parse_are_invalid(served) -> None:
    response = await served.rpc("SendMessage", {"message": "not a message"})

    assert response["error"]["code"] == INVALID_PARAMS


@pytest.mark.parametrize("announced", [None, "0.3", "2.0"], ids=["missing", "0.3", "2.0"])
async def test_a_1_0_method_needs_a2a_version_1_x(served, announced) -> None:
    """a2a-sdk reads a missing ``A2A-Version`` as 0.3, which no 1.0 method serves."""
    response = await served.rpc("GetTask", {"id": "t"}, version=announced)

    assert response["error"]["code"] == VERSION_NOT_SUPPORTED


async def test_a_message_to_a_completed_task_is_refused(served) -> None:
    task = (await served.send("hello"))["result"]["task"]

    response = await served.send("again", task_id=task["id"], context_id=task["contextId"])

    assert response["error"]["code"] == UNSUPPORTED_OPERATION
    assert "TASK_STATE_COMPLETED" in response["error"]["message"]


async def test_the_extended_card_is_unsupported(served) -> None:
    """The card does not declare an extended card."""
    response = await served.rpc("GetExtendedAgentCard", {})

    assert response["error"]["code"] == UNSUPPORTED_OPERATION


async def test_the_a2a_0_3_extended_card_is_refused(served) -> None:
    response = await served.rpc("agent/getAuthenticatedExtendedCard", None, version=None)

    assert "extended card" in response["error"]["message"]

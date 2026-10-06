"""A params parse failure reads the same on a standard method and on a Context extension method."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.asyncio(loop_scope="module")]

INVALID_PARAMS = -32602


def _shape(error: dict) -> tuple:
    """The parts of an error a client relies on: the code and the ErrorInfo detail."""
    [detail] = error["data"]
    return (
        error["code"],
        detail["@type"],
        detail["reason"],
        detail["domain"],
        sorted(detail["metadata"]),
    )


async def test_a_standard_and_a_context_method_report_a_parse_failure_alike(served) -> None:
    standard = await served.rpc("GetTask", {"id": {"not": "a string"}})
    context = await served.rpc("GetContext", {"contextId": " "})

    assert _shape(standard["error"]) == _shape(context["error"]) == (
        INVALID_PARAMS,
        "type.googleapis.com/google.rpc.ErrorInfo",
        "INVALID_PARAMS",
        "a2a-protocol.org",
        ["parseError"],
    )


async def test_a_context_method_refuses_another_a2a_version_as_a_standard_method_does(served) -> None:
    standard = await served.rpc("GetTask", {"id": "t"}, version="2.0")
    context = await served.rpc("GetContexts", {}, version="2.0")

    assert standard["error"]["code"] == context["error"]["code"] == -32009


async def test_a_context_method_without_a2a_version_is_served(served) -> None:
    response = await served.post({"jsonrpc": "2.0", "id": 1, "method": "GetContexts"}, version=None)

    assert response.json()["result"] == []
    assert response.headers["A2A-Extensions"]

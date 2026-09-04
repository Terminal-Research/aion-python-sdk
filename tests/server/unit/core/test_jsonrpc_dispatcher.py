"""Wire-format tests for the Aion JSON-RPC dispatcher."""

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import Mock

import pytest
from sse_starlette.sse import EventSourceResponse

from aion.server.core.app.handlers.jsonrpc_dispatcher import (
    AionJsonRpcDispatcher,
)


@pytest.mark.asyncio
async def test_streaming_response_uses_lf_event_delimiters() -> None:
    """Keep consecutive JSON-RPC events distinct through HTTP tunnels."""

    async def results() -> AsyncGenerator[dict[str, Any], None]:
        yield {'jsonrpc': '2.0', 'id': 1, 'result': {'sequence': 1}}
        yield {'jsonrpc': '2.0', 'id': 1, 'result': {'sequence': 2}}

    dispatcher = AionJsonRpcDispatcher(request_handler=Mock())
    response = dispatcher._create_response(Mock(), results())
    messages: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    assert isinstance(response, EventSourceResponse)
    await response._stream_response(send)

    body = b''.join(
        message.get('body', b'')
        for message in messages
        if message['type'] == 'http.response.body'
    )
    assert b'\r' not in body
    assert body.count(b'\n\ndata: ') == 1
    assert body.endswith(b'\n\n')

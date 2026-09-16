"""Behavioral tests for proxy response streaming and cleanup."""

import asyncio
from typing import AsyncIterator

import httpx
import pytest
from starlette.requests import ClientDisconnect, Request
from starlette.responses import StreamingResponse

from aion.proxy.handlers import RequestHandler


class ControlledByteStream(httpx.AsyncByteStream):
    """Expose independently released chunks and record stream closure."""

    def __init__(self, chunks: list[bytes]) -> None:
        """Initialize one release gate for every response chunk.

        Args:
            chunks: Raw response chunks yielded in order.
        """
        self.chunks = chunks
        self.releases = [asyncio.Event() for _ in chunks]
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        """Yield each chunk only after its corresponding gate opens."""
        for release, chunk in zip(self.releases, self.chunks):
            await release.wait()
            yield chunk

    async def aclose(self) -> None:
        """Record that the proxy released the upstream connection."""
        self.closed = True


def proxy_request(body: bytes = b'{}') -> Request:
    """Create a Starlette request suitable for direct handler invocation.

    Args:
        body: Request body returned by the ASGI receive callable.

    Returns:
        Request with one complete body frame.
    """
    delivered = False

    async def receive() -> dict:
        nonlocal delivered
        if delivered:
            return {'type': 'http.disconnect'}
        delivered = True
        return {
            'type': 'http.request',
            'body': body,
            'more_body': False,
        }

    return Request(
        {
            'type': 'http',
            'asgi': {'version': '3.0', 'spec_version': '2.4'},
            'http_version': '1.1',
            'method': 'POST',
            'scheme': 'http',
            'path': '/agents/example/',
            'raw_path': b'/agents/example/',
            'query_string': b'',
            'headers': [(b'content-type', b'application/json')],
            'client': ('127.0.0.1', 50000),
            'server': ('proxy', 8000),
        },
        receive,
    )


@pytest.mark.asyncio
async def test_forward_request_streams_without_upstream_framing() -> None:
    """Forward SSE chunks as they arrive and establish fresh framing."""
    first = b'data: {"result":"working"}\n\n'
    second = b'data: {"result":"completed"}\n\n'
    stream = ControlledByteStream([first, second])
    received_request: httpx.Request | None = None

    async def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal received_request
        received_request = request
        return httpx.Response(
            200,
            headers={
                'connection': 'keep-alive, x-hop',
                'content-length': '9999',
                'content-type': 'text/event-stream; charset=utf-8',
                'date': 'Wed, 12 Aug 2026 00:00:00 GMT',
                'server': 'upstream-server',
                'transfer-encoding': 'chunked',
                'x-hop': 'remove-me',
                'x-upstream': 'preserve-me',
            },
            stream=stream,
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(upstream)
    ) as client:
        handler = RequestHandler({'example': 'http://agent:8001'}, client)
        response = await handler.forward_request(
            'example',
            '',
            proxy_request(),
        )

        assert isinstance(response, StreamingResponse)
        assert response.headers['content-type'] == (
            'text/event-stream; charset=utf-8'
        )
        assert response.headers['x-upstream'] == 'preserve-me'
        for removed_header in (
            'connection',
            'content-length',
            'date',
            'server',
            'transfer-encoding',
            'x-hop',
        ):
            assert removed_header not in response.headers

        messages: list[dict] = []
        response_started = asyncio.Event()
        first_chunk_sent = asyncio.Event()

        async def receive() -> dict:
            await asyncio.Future()

        async def send(message: dict) -> None:
            messages.append(message)
            if message['type'] == 'http.response.start':
                response_started.set()
            elif message.get('body') == first:
                first_chunk_sent.set()

        response_task = asyncio.create_task(
            response(
                {
                    'type': 'http',
                    'asgi': {'version': '3.0', 'spec_version': '2.4'},
                },
                receive,
                send,
            )
        )
        await asyncio.wait_for(response_started.wait(), timeout=1)
        assert not first_chunk_sent.is_set()

        stream.releases[0].set()
        await asyncio.wait_for(first_chunk_sent.wait(), timeout=1)
        assert not response_task.done()

        stream.releases[1].set()
        await asyncio.wait_for(response_task, timeout=1)

    assert received_request is not None
    assert received_request.url == 'http://agent:8001/'
    assert await received_request.aread() == b'{}'
    assert b''.join(
        message.get('body', b'')
        for message in messages
        if message['type'] == 'http.response.body'
    ) == first + second
    assert stream.closed


@pytest.mark.asyncio
async def test_downstream_disconnect_closes_upstream_stream() -> None:
    """Release the upstream connection when the downstream client leaves."""
    stream = ControlledByteStream([b'data: {}\n\n'])

    async def upstream(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={'content-type': 'text/event-stream'},
            stream=stream,
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(upstream)
    ) as client:
        handler = RequestHandler({'example': 'http://agent:8001'}, client)
        response = await handler.forward_request(
            'example',
            '',
            proxy_request(),
        )
        stream.releases[0].set()

        async def receive() -> dict:
            await asyncio.Future()

        async def send(message: dict) -> None:
            if message['type'] == 'http.response.body':
                raise OSError('downstream disconnected')

        with pytest.raises(ClientDisconnect):
            await response(
                {
                    'type': 'http',
                    'asgi': {'version': '3.0', 'spec_version': '2.4'},
                },
                receive,
                send,
            )

    assert stream.closed

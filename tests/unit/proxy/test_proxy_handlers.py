"""RequestHandler forwarding contract -- one test per numbered guarantee.

See ``contract.py`` for the full statement.
"""

import asyncio
from typing import AsyncIterator

import httpx
import pytest
from fastapi import Request
from starlette.requests import ClientDisconnect
from starlette.responses import StreamingResponse

from aion.proxy.exceptions import (
    AgentNotFoundException,
    AgentTimeoutException,
    AgentUnavailableException,
)
from aion.proxy.handlers import RequestHandler

AGENT_ID = "test-agent"
AGENT_URL = "http://agent.local:8001"


def _make_request(
    method: str = "POST",
    body: bytes = b"{}",
    query_string: bytes = b"",
    extra_headers: list[tuple[bytes, bytes]] | None = None,
) -> Request:
    scope = {
        "type": "http",
        "method": method,
        "path": "/",
        "query_string": query_string,
        "headers": [
            (b"host", b"proxy.local"),
            (b"content-type", b"application/json"),
            (b"x-custom-request", b"yes"),
            *(extra_headers or []),
        ],
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def _make_handler(transport_handler) -> RequestHandler:
    client = httpx.AsyncClient(transport=httpx.MockTransport(transport_handler))
    return RequestHandler({AGENT_ID: AGENT_URL}, client)


async def _collect(response: StreamingResponse) -> list[bytes]:
    return [chunk async for chunk in response.body_iterator]


class _ControlledByteStream(httpx.AsyncByteStream):
    """Expose independently released chunks and record stream closure."""

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.releases = [asyncio.Event() for _ in chunks]
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for release, chunk in zip(self.releases, self.chunks):
            await release.wait()
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def _asgi_request(body: bytes = b"{}") -> Request:
    """Full ASGI request for tests that drive the response callable directly."""
    delivered = False

    async def receive() -> dict:
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/agents/example/",
            "raw_path": b"/agents/example/",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 50000),
            "server": ("proxy", 8000),
        },
        receive,
    )


# -- G1: streaming relay --------------------------------------------------


async def test_g1_chunks_are_relayed_without_buffering() -> None:
    release = asyncio.Event()

    async def chunks():
        yield b"first"
        await release.wait()
        yield b"second"

    handler = _make_handler(
        lambda request: httpx.Response(200, content=chunks())
    )

    response = await handler.forward_request(AGENT_ID, "", _make_request())
    iterator = response.body_iterator.__aiter__()

    first = await asyncio.wait_for(iterator.__anext__(), timeout=1.0)
    assert first == b"first"
    assert not release.is_set()

    release.set()
    assert await asyncio.wait_for(iterator.__anext__(), timeout=1.0) == b"second"
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(iterator.__anext__(), timeout=1.0)


# -- G2: response header hygiene ------------------------------------------


async def test_g2_framing_and_connection_listed_response_headers_stripped() -> None:
    handler = _make_handler(
        lambda request: httpx.Response(
            200,
            content=b"body",
            headers={
                "connection": "keep-alive, x-hop",
                "content-length": "9999",
                "content-type": "text/event-stream; charset=utf-8",
                "date": "Wed, 12 Aug 2026 00:00:00 GMT",
                "server": "upstream-server",
                "transfer-encoding": "chunked",
                "x-hop": "remove-me",
                "x-upstream": "preserve-me",
            },
        )
    )

    response = await handler.forward_request(AGENT_ID, "", _make_request())

    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    assert response.headers["x-upstream"] == "preserve-me"
    for removed in (
        "connection",
        "content-length",
        "date",
        "server",
        "transfer-encoding",
        "x-hop",
    ):
        assert removed not in response.headers


# -- G3: request header hygiene -------------------------------------------


async def test_g3_hop_by_hop_request_headers_are_not_forwarded() -> None:
    seen: dict[str, str | None] = {}

    def transport_handler(request: httpx.Request) -> httpx.Response:
        seen.update(
            {key.lower(): value for key, value in request.headers.items()}
        )
        return httpx.Response(200, content=b"ok")

    handler = _make_handler(transport_handler)
    request = _make_request(
        body=b'{"a": 1}',
        extra_headers=[
            (b"connection", b"keep-alive"),
            (b"keep-alive", b"timeout=5"),
            (b"transfer-encoding", b"chunked"),
            (b"te", b"trailers"),
            (b"upgrade", b"websocket"),
            (b"proxy-authorization", b"Basic c2VjcmV0"),
        ],
    )

    await handler.forward_request(AGENT_ID, "", request)

    for dropped in (
        "keep-alive",
        "transfer-encoding",
        "te",
        "upgrade",
        "proxy-authorization",
    ):
        assert dropped not in seen, f"{dropped} was forwarded upstream"
    assert seen.get("host") != "proxy.local"


async def test_g3_ordinary_request_headers_reach_the_agent() -> None:
    seen: dict[str, str | None] = {}

    def transport_handler(request: httpx.Request) -> httpx.Response:
        seen.update(
            {key.lower(): value for key, value in request.headers.items()}
        )
        return httpx.Response(200, content=b"ok")

    handler = _make_handler(transport_handler)
    request = _make_request(
        extra_headers=[(b"authorization", b"Bearer token-123")]
    )

    await handler.forward_request(AGENT_ID, "", request)

    assert seen["x-custom-request"] == "yes"
    assert seen["content-type"] == "application/json"
    assert seen["authorization"] == "Bearer token-123"


# -- G4: content-length integrity -----------------------------------------


async def test_g4_content_length_matches_the_forwarded_body() -> None:
    seen: dict[str, str | None] = {}

    def transport_handler(request: httpx.Request) -> httpx.Response:
        seen["content-length"] = request.headers.get("content-length")
        return httpx.Response(200, content=b"ok")

    handler = _make_handler(transport_handler)
    body = b'{"hello": "world"}'
    request = _make_request(
        body=body, extra_headers=[(b"content-length", b"99999")]
    )

    await handler.forward_request(AGENT_ID, "", request)

    assert seen["content-length"] == str(len(body))


# -- G5: request forwarding fidelity --------------------------------------


async def test_g5_method_body_and_path_forwarded_to_agent_url() -> None:
    seen: dict = {}

    def transport(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = request.content
        return httpx.Response(200, content=b"ok")

    handler = _make_handler(transport)

    await handler.forward_request(
        AGENT_ID, "some/path", _make_request(body=b'{"a":1}')
    )

    assert seen["method"] == "POST"
    assert seen["url"] == f"{AGENT_URL}/some/path"
    assert seen["body"] == b'{"a":1}'


# -- G6: query string forwarding ------------------------------------------


async def test_g6_query_parameters_appended_to_target_url() -> None:
    seen: dict = {}

    def transport(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, content=b"ok")

    handler = _make_handler(transport)

    await handler.forward_request(
        AGENT_ID, "endpoint", _make_request(query_string=b"foo=1&bar=2")
    )

    assert seen["url"] == f"{AGENT_URL}/endpoint?foo=1&bar=2"


# -- G7: unknown agent error ----------------------------------------------


async def test_g7_unknown_agent_raises_not_found() -> None:
    handler = _make_handler(lambda request: httpx.Response(200))

    with pytest.raises(AgentNotFoundException):
        await handler.forward_request("missing", "", _make_request())


# -- G8: connection failure error ------------------------------------------


async def test_g8_connect_error_maps_to_unavailable() -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    handler = _make_handler(transport)

    with pytest.raises(AgentUnavailableException):
        await handler.forward_request(AGENT_ID, "", _make_request())


# -- G9: timeout error -----------------------------------------------------


async def test_g9_timeout_maps_to_timeout_exception() -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    handler = _make_handler(transport)

    with pytest.raises(AgentTimeoutException):
        await handler.forward_request(AGENT_ID, "", _make_request())


# -- G10: mid-stream error absorption --------------------------------------


async def test_g10_mid_stream_error_ends_body_without_raising() -> None:
    async def chunks():
        yield b"first"
        raise httpx.ReadError("connection lost")

    handler = _make_handler(
        lambda request: httpx.Response(200, content=chunks())
    )

    response = await handler.forward_request(AGENT_ID, "", _make_request())

    assert await _collect(response) == [b"first"]


# -- G11: upstream connection release --------------------------------------


async def test_g11_downstream_disconnect_closes_upstream_stream() -> None:
    stream = _ControlledByteStream([b"data: {}\n\n"])

    async def upstream(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=stream,
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(upstream)
    ) as client:
        handler = RequestHandler({"example": "http://agent:8001"}, client)
        response = await handler.forward_request(
            "example", "", _asgi_request()
        )
        stream.releases[0].set()

        async def receive() -> dict:
            await asyncio.Future()

        async def send(message: dict) -> None:
            if message["type"] == "http.response.body":
                raise OSError("downstream disconnected")

        with pytest.raises(ClientDisconnect):
            await response(
                {
                    "type": "http",
                    "asgi": {"version": "3.0", "spec_version": "2.4"},
                },
                receive,
                send,
            )

    assert stream.closed

"""Tests for the proxy RequestHandler's streaming forwarding."""

import asyncio

import httpx
import pytest
from fastapi import Request
from fastapi.responses import StreamingResponse

from aion.proxy.exceptions import (
    AgentNotFoundException,
    AgentTimeoutException,
    AgentUnavailableException,
)
from aion.proxy.handlers import RequestHandler

AGENT_ID = "test-agent"
AGENT_URL = "http://agent.local:8001"


def make_request(
    method: str = "POST",
    body: bytes = b"{}",
    extra_headers: list[tuple[bytes, bytes]] | None = None,
) -> Request:
    scope = {
        "type": "http",
        "method": method,
        "path": "/",
        "query_string": b"",
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


def make_handler(transport_handler) -> RequestHandler:
    client = httpx.AsyncClient(transport=httpx.MockTransport(transport_handler))
    return RequestHandler({AGENT_ID: AGENT_URL}, client)


async def collect(response: StreamingResponse) -> list[bytes]:
    return [chunk async for chunk in response.body_iterator]


class TestForwardRequestStreaming:

    async def test_relays_upstream_chunks_and_status(self):
        async def chunks():
            yield b"event: one\n\n"
            yield b"event: two\n\n"

        handler = make_handler(
            lambda request: httpx.Response(
                200, content=chunks(), headers={"content-type": "text/event-stream"}
            )
        )

        response = await handler.forward_request(AGENT_ID, "", make_request())

        assert isinstance(response, StreamingResponse)
        assert response.status_code == 200
        assert await collect(response) == [b"event: one\n\n", b"event: two\n\n"]

    async def test_chunk_is_relayed_before_upstream_completes(self):
        release = asyncio.Event()

        async def chunks():
            yield b"first"
            await release.wait()
            yield b"second"

        handler = make_handler(
            lambda request: httpx.Response(200, content=chunks())
        )

        response = await handler.forward_request(AGENT_ID, "", make_request())
        iterator = response.body_iterator.__aiter__()

        # The first chunk must arrive while the upstream stream is still
        # open - a buffering proxy would hang here until `release` is set
        first = await asyncio.wait_for(iterator.__anext__(), timeout=1.0)
        assert first == b"first"
        assert not release.is_set()

        release.set()
        assert await asyncio.wait_for(iterator.__anext__(), timeout=1.0) == b"second"
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(iterator.__anext__(), timeout=1.0)

    async def test_framing_headers_dropped_and_rest_preserved(self):
        handler = make_handler(
            lambda request: httpx.Response(
                200,
                content=b"body",
                headers={
                    "content-type": "application/json",
                    "x-custom-response": "kept",
                    "connection": "keep-alive",
                    "content-length": "4",
                },
            )
        )

        response = await handler.forward_request(AGENT_ID, "", make_request())

        assert response.headers["content-type"] == "application/json"
        assert response.headers["x-custom-response"] == "kept"
        assert "connection" not in response.headers
        assert "transfer-encoding" not in response.headers
        # content-length appears only if starlette re-derives it, never
        # copied from upstream; StreamingResponse leaves it unset
        assert "content-length" not in response.headers

    async def test_forwards_method_body_and_headers_without_host(self):
        seen: dict = {}

        def transport(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["body"] = request.content
            seen["x-custom-request"] = request.headers.get("x-custom-request")
            return httpx.Response(200, content=b"ok")

        handler = make_handler(transport)

        await handler.forward_request(
            AGENT_ID, "some/path", make_request(body=b'{"a":1}')
        )

        assert seen["method"] == "POST"
        assert seen["url"] == f"{AGENT_URL}/some/path"
        assert seen["body"] == b'{"a":1}'
        assert seen["x-custom-request"] == "yes"

    async def test_mid_stream_error_ends_body_without_raising(self):
        async def chunks():
            yield b"first"
            raise httpx.ReadError("connection lost")

        handler = make_handler(
            lambda request: httpx.Response(200, content=chunks())
        )

        response = await handler.forward_request(AGENT_ID, "", make_request())

        assert await collect(response) == [b"first"]


class TestForwardRequestErrors:

    async def test_unknown_agent_raises_not_found(self):
        handler = make_handler(lambda request: httpx.Response(200))

        with pytest.raises(AgentNotFoundException):
            await handler.forward_request("missing", "", make_request())

    async def test_connect_error_maps_to_unavailable(self):
        def transport(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        handler = make_handler(transport)

        with pytest.raises(AgentUnavailableException):
            await handler.forward_request(AGENT_ID, "", make_request())

    async def test_timeout_maps_to_timeout_exception(self):
        def transport(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        handler = make_handler(transport)

        with pytest.raises(AgentTimeoutException):
            await handler.forward_request(AGENT_ID, "", make_request())


class TestRequestHeaderHygiene:
    """Headers describing the client's hop to the proxy end at the proxy.

    Forwarding them upstream lets the client's own framing contradict the
    request httpx builds — `content-length` in particular is recomputed from
    the body the proxy sends.
    """

    async def test_hop_by_hop_request_headers_are_not_forwarded(self):
        seen: dict[str, str | None] = {}

        def transport_handler(request: httpx.Request) -> httpx.Response:
            seen.update(
                {key.lower(): value for key, value in request.headers.items()}
            )
            return httpx.Response(200, content=b"ok")

        handler = make_handler(transport_handler)
        request = make_request(
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
        # The proxy addresses the agent, not the name the client used for us.
        assert seen.get("host") != "proxy.local"

    async def test_content_length_matches_the_forwarded_body(self):
        """A stale `content-length` from the client must not survive a body the
        proxy re-frames."""
        seen: dict[str, str | None] = {}

        def transport_handler(request: httpx.Request) -> httpx.Response:
            seen["content-length"] = request.headers.get("content-length")
            return httpx.Response(200, content=b"ok")

        handler = make_handler(transport_handler)
        body = b'{"hello": "world"}'
        request = make_request(
            body=body, extra_headers=[(b"content-length", b"99999")]
        )

        await handler.forward_request(AGENT_ID, "", request)

        assert seen["content-length"] == str(len(body))

    async def test_ordinary_headers_still_reach_the_agent(self):
        seen: dict[str, str | None] = {}

        def transport_handler(request: httpx.Request) -> httpx.Response:
            seen.update(
                {key.lower(): value for key, value in request.headers.items()}
            )
            return httpx.Response(200, content=b"ok")

        handler = make_handler(transport_handler)
        request = make_request(
            extra_headers=[(b"authorization", b"Bearer token-123")]
        )

        await handler.forward_request(AGENT_ID, "", request)

        assert seen["x-custom-request"] == "yes"
        assert seen["content-type"] == "application/json"
        assert seen["authorization"] == "Bearer token-123"

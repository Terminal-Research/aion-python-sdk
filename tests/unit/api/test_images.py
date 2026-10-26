"""Verify image wire handling, attribution, and ownership without paid calls."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from aion.api import images
from aion.core.exceptions import (
    AionAuthenticationError, AionDaemonIdentityRequired, AionImageError,
)
from aion.core.runtime.context import AionRuntimeContext, ForwardedAttribution


RESULT = {
    "created": 1,
    "data": [{"b64_json": "YQ==", "media_type": "image/png"}],
    "usage": {"cost": 12.5},
}
COMPLETED = {"type": "image_generation.completed", **RESULT["data"][0], "usage": RESULT["usage"]}


class Body(httpx.SyncByteStream, httpx.AsyncByteStream):
    """Record lazy reads and closing through the real HTTPX response lifecycle."""

    def __init__(self, chunks):
        self.chunks = chunks
        self.reads = 0
        self.closed = False

    def __iter__(self):
        for chunk in self.chunks:
            self.reads += 1
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk

    async def __aiter__(self):
        for chunk in self:
            yield chunk

    def close(self):
        self.closed = True

    async def aclose(self):
        self.close()


@pytest.fixture
def auth(monkeypatch):
    state = SimpleNamespace(context=None, tokens=0)

    def token():
        state.tokens += 1
        return f"token-{state.tokens}"

    async def token_async():
        return token()

    monkeypatch.setattr(images, "aion_model_api_key", token)
    monkeypatch.setattr(images, "aion_jwt_api_key", token_async)
    monkeypatch.setattr(images, "aion_model_base_url", lambda: "https://api.example.test/mounted/v1")
    monkeypatch.setattr("aion.api.callback_attribution.get_aion_runtime_context", lambda: state.context)
    return state


def event_bytes(event):
    return f"data: {json.dumps(event)}\n\n".encode()


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.asyncio
async def test_json_refreshes_credentials_and_attribution_per_request(auth, asynchronous):
    requests = []
    bodies = []

    def respond(request):
        requests.append(request)
        body = Body([json.dumps(RESULT).encode()])
        bodies.append(body)
        return httpx.Response(200, headers={"content-type": "application/json"}, stream=body)

    transport = httpx.MockTransport(respond)
    # Caller-owned transport settings must not replace Aion credentials.
    client_type = httpx.AsyncClient if asynchronous else httpx.Client
    client = client_type(transport=transport, auth=("old", "credentials"),
                         headers={"authorization": "Bearer stale", "X-Trace": "kept"})
    try:
        for carrier in ("first", "second", None):
            auth.context = AionRuntimeContext(callback_attribution=ForwardedAttribution(carrier)) if carrier else None
            if asynchronous:
                result = await images.generate_images_async("image/model", "A blue circle", client=client, n=1)
            else:
                result = images.generate_images("image/model", "A blue circle", client=client, n=1)
            assert result == RESULT
            assert not client.is_closed
        for index, (request, carrier) in enumerate(zip(requests, ("first", "second", None)), 1):
            assert str(request.url) == "https://api.example.test/mounted/v1/images"
            assert request.headers.get_list("Authorization") == [f"Bearer token-{index}"]
            assert request.headers["X-Trace"] == "kept"
            assert request.headers.get("Aion-Usage-Attribution") == carrier
            assert "Aion-Principal-Selector" not in request.headers
            assert "Aion-Caller-Id" not in request.headers
            assert json.loads(request.content) == {
                "model": "image/model", "prompt": "A blue circle", "stream": False, "n": 1,
            }
            assert request.extensions["timeout"]["read"] == 480.0
        assert all(body.closed for body in bodies)
    finally:
        await client.aclose() if asynchronous else client.close()


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("early_exit", [False, True])
@pytest.mark.asyncio
async def test_native_sse_is_lazy_and_closes_on_normal_or_early_exit(auth, asynchronous, early_exit):
    preview = {"type": "image_generation.partial_image", "b64_json": "YQ=="}
    body = Body([
        b': keepalive\r\nevent: preview\r\ndata: {"type":"image_generation.partial_image",\r\n'
        b'data: "b64_json":"YQ=="}\r\n\r\n',
        event_bytes(COMPLETED),
        b"data: [DONE]\n\n",
    ])
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream; charset=utf-8"}, stream=body)

    transport = httpx.MockTransport(respond)
    if asynchronous:
        async with httpx.AsyncClient(transport=transport) as client:
            async with images.stream_images_async("image/model", "Icon", client=client) as events:
                assert body.reads == 0
                assert await anext(events) == preview
                assert body.reads == 1
                if not early_exit:
                    assert [event async for event in events] == [COMPLETED]
            assert not client.is_closed
    else:
        with httpx.Client(transport=transport) as client:
            with images.stream_images("image/model", "Icon", client=client) as events:
                assert body.reads == 0
                assert next(events) == preview
                assert body.reads == 1
                if not early_exit:
                    assert list(events) == [COMPLETED]
            assert not client.is_closed
    assert body.closed
    assert body.reads == (1 if early_exit else 3)
    assert len(requests) == 1
    assert json.loads(requests[0].content)["stream"] is True


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.asyncio
async def test_json_fallback_emits_all_images_and_reports_aggregate_cost_once(auth, asynchronous):
    result = {**RESULT, "data": RESULT["data"] * 2}
    body = Body([json.dumps(result).encode()])
    transport = httpx.MockTransport(lambda _: httpx.Response(
        200, headers={"content-type": "application/json; charset=utf-8"}, stream=body,
    ))
    if asynchronous:
        async with httpx.AsyncClient(transport=transport) as client:
            async with images.stream_images_async("image/model", "Icon", client=client) as events:
                received = [event async for event in events]
    else:
        with httpx.Client(transport=transport) as client:
            with images.stream_images("image/model", "Icon", client=client) as events:
                received = list(events)
    assert [event["type"] for event in received] == ["image_generation.completed"] * 2
    assert [event["b64_json"] for event in received] == ["YQ=="] * 2
    assert [event.get("usage") for event in received] == [None, RESULT["usage"]]
    assert body.closed


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("tail", [
    b"", b"data: not-json\n\n", event_bytes({"type": "error", "error": {"message": "failed"}}),
    event_bytes({"type": "unknown"}),
])
@pytest.mark.asyncio
async def test_stream_failure_preserves_completed_output_and_closes(auth, asynchronous, tail):
    body = Body([event_bytes(COMPLETED), tail])
    transport = httpx.MockTransport(lambda _: httpx.Response(
        200, headers={"content-type": "text/event-stream"}, stream=body,
    ))
    if asynchronous:
        async with httpx.AsyncClient(transport=transport) as client:
            async with images.stream_images_async("image/model", "Icon", client=client) as events:
                assert await anext(events) == COMPLETED
                with pytest.raises(AionImageError):
                    await anext(events)
    else:
        with httpx.Client(transport=transport) as client:
            with images.stream_images("image/model", "Icon", client=client) as events:
                assert next(events) == COMPLETED
                with pytest.raises(AionImageError):
                    next(events)
    assert body.closed


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("missing_daemon", [False, True])
@pytest.mark.asyncio
async def test_http_failure_is_not_retried_and_preserves_daemon_error(auth, asynchronous, stream, missing_daemon):
    calls = []
    code = "daemon_identity_required" if missing_daemon else "image_unavailable"

    def respond(request):
        calls.append(request)
        return httpx.Response(503, json={"error": {"code": code, "resourceId": "deployment-1"}})

    transport = httpx.MockTransport(respond)
    expected = AionDaemonIdentityRequired if missing_daemon else httpx.HTTPStatusError
    with pytest.raises(expected):
        if asynchronous:
            async with httpx.AsyncClient(transport=transport) as client:
                if stream:
                    async with images.stream_images_async("image/model", "Icon", client=client):
                        pytest.fail("An HTTP error must fail before yielding an iterator")
                else:
                    await images.generate_images_async("image/model", "Icon", client=client)
        else:
            with httpx.Client(transport=transport) as client:
                if stream:
                    with images.stream_images("image/model", "Icon", client=client):
                        pytest.fail("An HTTP error must fail before yielding an iterator")
                else:
                    images.generate_images("image/model", "Icon", client=client)
    assert len(calls) == 1


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.asyncio
async def test_active_request_without_attribution_does_not_fall_back_to_daemon(auth, asynchronous):
    auth.context = AionRuntimeContext()
    transport = httpx.MockTransport(lambda _: pytest.fail("Invalid attribution reached HTTP"))
    with pytest.raises(AionAuthenticationError):
        if asynchronous:
            async with httpx.AsyncClient(transport=transport) as client:
                await images.generate_images_async("image/model", "Icon", client=client)
        else:
            with httpx.Client(transport=transport) as client:
                images.generate_images("image/model", "Icon", client=client)
    assert auth.tokens == 0


@pytest.mark.asyncio
async def test_async_body_cancellation_closes_stream_without_retry(auth):
    body = Body([event_bytes(COMPLETED), asyncio.CancelledError()])
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(asyncio.CancelledError):
            async with images.stream_images_async("image/model", "Icon", client=client) as events:
                assert await anext(events) == COMPLETED
                await anext(events)
        assert not client.is_closed
    assert body.closed
    assert len(calls) == 1


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.asyncio
async def test_default_client_and_response_are_closed(auth, monkeypatch, asynchronous):
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=RESULT))
    if asynchronous:
        client = httpx.AsyncClient(transport=transport)
        monkeypatch.setattr(images.httpx, "AsyncClient", lambda: client)
        assert await images.generate_images_async("image/model", "Icon") == RESULT
    else:
        client = httpx.Client(transport=transport)
        monkeypatch.setattr(images.httpx, "Client", lambda: client)
        assert images.generate_images("image/model", "Icon") == RESULT
    assert client.is_closed


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("media,payload,stream", [
    ("text/plain", b"not an image response", False),
    ("application/json", b'{"data":[]}', False),
    ("application/json", b"[]", False),
    ("application/json", b"invalid", True),
    ("text/event-stream", b"data: [DONE]\n\n", True),
    ("text/event-stream", event_bytes({"type": "image_generation.completed", "b64_json": ""}), True),
])
@pytest.mark.asyncio
async def test_invalid_result_closes_without_reporting_success(auth, asynchronous, media, payload, stream):
    body = Body([payload])
    transport = httpx.MockTransport(lambda _: httpx.Response(
        200, headers={"content-type": media}, stream=body,
    ))
    with pytest.raises(AionImageError):
        if asynchronous:
            async with httpx.AsyncClient(transport=transport) as client:
                if stream:
                    async with images.stream_images_async("image/model", "Icon", client=client) as events:
                        assert not [event async for event in events]
                else:
                    await images.generate_images_async("image/model", "Icon", client=client)
        else:
            with httpx.Client(transport=transport) as client:
                if stream:
                    with images.stream_images("image/model", "Icon", client=client) as events:
                        assert not list(events)
                else:
                    images.generate_images("image/model", "Icon", client=client)
    assert body.closed

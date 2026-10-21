"""Exercise Decisions through the real OpenAI transport without paid calls."""

import asyncio
import json

import httpx
import pytest
from openai import AsyncOpenAI, InternalServerError, OpenAI

from aion.api.decisions import (
    DecisionRequest,
    create_decision,
    create_decision_async,
)
from aion.core.exceptions import AionDaemonIdentityRequired
from aion.core.runtime.context import AionRuntimeContext, ForwardedAttribution


REQUEST: DecisionRequest = {
    "model": "typesafe/jev-1.14",
    "state": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,YQ=="}}],
    "questions": {
        "visible": {"type": "noul", "instructions": "Is a person visible?"},
    },
}
RESPONSE = {
    "model": "typesafe/jev-1.14",
    "answers": {"visible": {"type": "noul", "noul": 0.75}},
    "usage": {"cost": 0.000003},
}


def changing_context(monkeypatch):
    contexts = iter(
        AionRuntimeContext(callback_attribution=ForwardedAttribution(value))
        for value in ("first", "second")
    )
    monkeypatch.setattr(
        "aion.api.callback_attribution.get_aion_runtime_context", lambda: next(contexts),
    )


@pytest.mark.parametrize("base_url", [
    "https://api.example.test/v1", "https://api.example.test/mounted/v1/",
])
def test_sync_preserves_payload_and_refreshes_credentials_and_scope(monkeypatch, base_url):
    changing_context(monkeypatch)
    tokens = iter(("token-1", "token-2"))
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=RESPONSE)

    with OpenAI(
        base_url=base_url, api_key=lambda: next(tokens), timeout=47.0,
        http_client=httpx.Client(transport=httpx.MockTransport(respond)),
    ) as client:
        for _ in range(2):
            response = create_decision(client, REQUEST)
            assert response["answers"]["visible"]["noul"] == 0.75
            assert response["usage"]["cost"] == 0.000003
        assert not client.is_closed()
        assert client.max_retries == 2

    expected_url = base_url.rstrip("/").removesuffix("v1") + "alpha/decisions"
    for request, token, scope in zip(requests, ("token-1", "token-2"), ("first", "second")):
        assert str(request.url) == expected_url
        assert json.loads(request.content) == REQUEST
        assert request.headers["Authorization"] == f"Bearer {token}"
        assert request.headers["Aion-Usage-Attribution"] == scope
        assert "Aion-Principal-Selector" not in request.headers
        assert request.extensions["timeout"]["read"] == 47.0


@pytest.mark.asyncio
async def test_async_preserves_missing_cost_and_refreshes_scope(monkeypatch):
    changing_context(monkeypatch)
    tokens = iter(("token-1", "token-2"))
    requests = []

    async def api_key():
        return next(tokens)

    async def respond(request):
        requests.append(request)
        return httpx.Response(200, json={**RESPONSE, "usage": {"input_tokens": None}})

    async with AsyncOpenAI(
        base_url="https://api.example.test/mounted/v1", api_key=api_key,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    ) as client:
        for _ in range(2):
            response = await create_decision_async(client, REQUEST)
            assert response["usage"] == {"input_tokens": None}
        assert not client.is_closed()
        assert client.max_retries == 2

    for request, token, scope in zip(requests, ("token-1", "token-2"), ("first", "second")):
        assert str(request.url) == "https://api.example.test/mounted/alpha/decisions"
        assert json.loads(request.content) == REQUEST
        assert request.headers["Authorization"] == f"Bearer {token}"
        assert request.headers["Aion-Usage-Attribution"] == scope


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("missing_daemon", [False, True])
@pytest.mark.asyncio
async def test_errors_preserve_configuration_failure_and_never_retry(asynchronous, missing_daemon):
    requests = []
    code = "daemon_identity_required" if missing_daemon else "decisions_unavailable"
    payload = {"error": {"code": code, "message": "Unavailable", "resourceId": "deployment-1"}}

    def respond(request):
        requests.append(request)
        return httpx.Response(503, json=payload)

    expected = AionDaemonIdentityRequired if missing_daemon else InternalServerError
    with pytest.raises(expected) as failure:
        if asynchronous:
            async with AsyncOpenAI(
                base_url="https://api.example.test/v1", api_key="token", max_retries=4,
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
            ) as client:
                await create_decision_async(client, REQUEST)
        else:
            with OpenAI(
                base_url="https://api.example.test/v1", api_key="token", max_retries=4,
                http_client=httpx.Client(transport=httpx.MockTransport(respond)),
            ) as client:
                create_decision(client, REQUEST)
    assert len(requests) == 1
    if missing_daemon:
        assert failure.value.resource_id == "deployment-1"


@pytest.mark.asyncio
async def test_async_cancellation_propagates_without_retries():
    requests = []

    async def cancel(request):
        requests.append(request)
        raise asyncio.CancelledError

    async with AsyncOpenAI(
        base_url="https://api.example.test/v1", api_key="token",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(cancel)),
    ) as client:
        with pytest.raises(asyncio.CancelledError):
            await create_decision_async(client, REQUEST)
        assert not client.is_closed()
    assert len(requests) == 1

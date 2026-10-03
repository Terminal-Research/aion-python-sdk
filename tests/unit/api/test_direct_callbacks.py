"""Direct callbacks keep deployment credentials, isolation, and actionable errors."""

import asyncio
import json

import httpx
import pytest

from aion.api import model_service_client
from aion.api.callback_attribution import callback_headers
from aion.api.callback_errors import (
    async_callback_response_hook, callback_response_hook, raise_callback_error,
    reraise_callback_error,
)
from aion.core.exceptions import AionAuthenticationError, AionDaemonIdentityRequired
from aion.core.principal import Principal
from aion.core.runtime.context import AionRuntimeContext, DirectAttribution, ForwardedAttribution
from aion.mcp import aion_mcp_authorization_headers


@pytest.mark.parametrize("caller", [
    Principal("AionUser", "00000000-0000-0000-0000-000000000001"),
    Principal("AnonymousSession", "00000000-0000-0000-0000-000000000002"),
    Principal("ExternalAnonymous", "external-anonymous"),
])
def test_direct_model_and_mcp_use_version_credentials(monkeypatch, caller):
    context = AionRuntimeContext(callback_attribution=DirectAttribution(caller))
    monkeypatch.setattr("aion.api.callback_attribution.get_aion_runtime_context", lambda: context)
    assert aion_mcp_authorization_headers("version-token") == {
        "Authorization": "Bearer version-token", "Aion-Caller-Id": caller.subject,
    }
    request = httpx.Request("POST", "https://api.aion.test/v1/chat/completions")
    model_service_client._model_request_hook(lambda: "version-token")(request)
    assert request.headers["Authorization"] == "Bearer version-token"
    assert request.headers["Aion-Caller-Id"] == caller.subject
    assert "Aion-Usage-Attribution" not in request.headers
    assert "Aion-Principal-Selector" not in request.headers
    # Reapplying a hook must not invent a conflict for the same request.
    model_service_client.aion_model_request_hook(request)


def test_uncredentialed_direct_request_cannot_use_its_guest_token(monkeypatch):
    context = AionRuntimeContext(callback_attribution=DirectAttribution(
        Principal("ExternalAnonymous", "external-anonymous")))
    monkeypatch.setattr("aion.api.callback_attribution.get_aion_runtime_context", lambda: context)
    with pytest.raises(AionAuthenticationError, match="API token"):
        aion_mcp_authorization_headers(None)
    with pytest.raises(AionAuthenticationError, match="conflicts"):
        callback_headers(usage_attribution="stale-carrier")


@pytest.mark.parametrize("payload", [
    {"error": {"code": "daemon_identity_required", "type": "configuration_error"}},
    {"error": {"code": -32600, "data": {"code": "daemon_identity_required"}}},
])
def test_missing_daemon_has_stable_non_retryable_error(payload):
    response = httpx.Response(409, json=payload)
    with pytest.raises(AionDaemonIdentityRequired) as caught:
        callback_response_hook(response)
    assert caught.value.code == "daemon_identity_required"
    assert caught.value.retryable is False
    assert "Identity tab" in str(caught.value)


def test_unrelated_errors_are_not_reclassified():
    raise_callback_error({"error": {"code": "invalid_token"}})
    callback_response_hook(httpx.Response(403, text="Forbidden"))


async def test_successful_stream_is_not_buffered_by_error_hook():
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            pytest.fail("A successful event stream must not be consumed by the hook")
            yield b""
    response = httpx.Response(200, stream=Body())
    await async_callback_response_hook(response)
    assert not response.is_stream_consumed
    await response.aclose()


def test_wrapped_transport_groups_preserve_configuration_failure():
    required = AionDaemonIdentityRequired("Deployment", "deployment-1")
    with pytest.raises(AionDaemonIdentityRequired) as caught:
        reraise_callback_error(ExceptionGroup("MCP", [RuntimeError("transport"), required]))
    assert caught.value is required


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("mode", ["invoke", "stream", "responses_stream"])
def test_langchain_preserves_error_without_retry(monkeypatch, asynchronous, mode):
    from aion.langgraph.authoring import models
    calls = []
    def handle(request):
        calls.append(request)
        payload = {"error": {"code": "daemon_identity_required",
                             "message": "Select a daemon", "type": "configuration_error"}}
        if mode == "invoke":
            return httpx.Response(409, json=payload)
        event = "event: error\n" if mode == "responses_stream" else ""
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              text=event + "data: " + json.dumps(payload) + "\n\n")
    sync = httpx.Client(transport=httpx.MockTransport(handle), event_hooks={
        "request": [model_service_client._model_request_hook(lambda: "version-token")],
        "response": [callback_response_hook],
    })
    async_client = httpx.AsyncClient(transport=httpx.MockTransport(handle), event_hooks={
        "request": [model_service_client._async_model_request_hook(lambda: "version-token")],
        "response": [async_callback_response_hook],
    })
    monkeypatch.setattr(model_service_client, "_aion_model_http_client", lambda _: sync)
    monkeypatch.setattr(model_service_client, "_aion_model_async_http_client", lambda _: async_client)
    monkeypatch.setattr(models, "aion_openai_config", lambda: model_service_client.AionModelClientConfig(
        base_url="https://api.aion.test/v1", api_key="version-token"))
    monkeypatch.setattr("aion.api.callback_attribution.get_aion_runtime_context", lambda:
        AionRuntimeContext(callback_attribution=ForwardedAttribution("opaque")))
    model = models.aion_chat_openai("test-model", use_responses_api=mode == "responses_stream")

    async def consume():
        if mode == "invoke":
            await model.ainvoke("hello")
        else:
            async for _ in model.astream("hello"):
                pytest.fail("Missing configuration must not yield model output")

    try:
        with pytest.raises(AionDaemonIdentityRequired) as caught:
            if asynchronous:
                asyncio.run(consume())
            elif mode == "invoke":
                model.invoke("hello")
            else:
                list(model.stream("hello"))
        assert caught.value.retryable is False
        assert len(calls) == 1
    finally:
        sync.close()
        asyncio.run(async_client.aclose())

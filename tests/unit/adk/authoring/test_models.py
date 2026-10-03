import asyncio
import json
from types import ModuleType
import sys

import pytest
import httpx

from aion.adk.authoring.models import aion_lite_llm
import aion.adk.authoring.models as models
import aion.api.model_service_client as model_service_client
from aion.api.exceptions import AionModelPrincipalError
from aion.core.exceptions import AionDaemonIdentityRequired
from aion.core.runtime.context import AionRuntimeContext


class FakeConfig:
    def __init__(self):
        self.api_key = lambda: "jwt-token"

    def litellm_kwargs(self):
        return {
            "api_base": "https://api.example.test/v1",
            "api_key": self.api_key,
        }


def test_aion_lite_llm_configures_google_adk_litellm(monkeypatch):
    captured = {}

    class FakeLiteLlm:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class FakeLiteLLMClient:
        pass

    google = ModuleType("google")
    google_adk = ModuleType("google.adk")
    google_adk_models = ModuleType("google.adk.models")
    lite_llm = ModuleType("google.adk.models.lite_llm")
    lite_llm.LiteLlm = FakeLiteLlm
    lite_llm.LiteLLMClient = FakeLiteLLMClient

    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.adk", google_adk)
    monkeypatch.setitem(sys.modules, "google.adk.models", google_adk_models)
    monkeypatch.setitem(sys.modules, "google.adk.models.lite_llm", lite_llm)
    monkeypatch.setattr(models, "aion_openai_config", lambda: FakeConfig())
    monkeypatch.setattr(models, "aion_model_api_key", lambda: "fresh-jwt")
    monkeypatch.setattr(
        models,
        "aion_model_request_headers",
        lambda existing=None: {
            **(existing or {}),
            "Aion-Principal-Selector": "aion://agent/environment/fresh-env",
        },
    )

    result = aion_lite_llm(
        "model-id-from-control-plane",
        temperature=0.2,
    )

    assert isinstance(result, FakeLiteLlm)
    assert captured == {
        "model": "model-id-from-control-plane",
        "api_base": "https://api.example.test/v1",
        "llm_client": captured["llm_client"],
        "temperature": 0.2,
    }
    assert isinstance(captured["llm_client"], FakeLiteLLMClient)

    litellm = ModuleType("litellm")
    completion_calls = []
    acompletion_calls = []

    def completion(**kwargs):
        completion_calls.append(kwargs)
        return "completion"

    async def acompletion(**kwargs):
        acompletion_calls.append(kwargs)
        return "acompletion"

    litellm.completion = completion
    litellm.acompletion = acompletion
    monkeypatch.setitem(sys.modules, "litellm", litellm)

    sync_result = captured["llm_client"].completion(
        model="openai/model-id",
        messages=[],
        tools=None,
        api_key="stale-token",
    )
    async_result = asyncio.run(
        captured["llm_client"].acompletion(
            model="openai/model-id",
            messages=[],
            tools=None,
            api_key="stale-token",
        )
    )

    assert sync_result == "completion"
    assert async_result == "acompletion"
    assert completion_calls[0]["api_key"] == "fresh-jwt"
    assert acompletion_calls[0]["api_key"] == "fresh-jwt"
    assert completion_calls[0]["extra_headers"] == {
        "Aion-Principal-Selector": "aion://agent/environment/fresh-env",
    }
    assert acompletion_calls[0]["extra_headers"] == {
        "Aion-Principal-Selector": "aion://agent/environment/fresh-env",
    }


def _fake_adk_modules(monkeypatch):
    """Install stand-ins for google-adk and litellm, returning the litellm calls."""

    class FakeLiteLlm:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeLiteLLMClient:
        pass

    google = ModuleType("google")
    google_adk = ModuleType("google.adk")
    google_adk_models = ModuleType("google.adk.models")
    lite_llm = ModuleType("google.adk.models.lite_llm")
    lite_llm.LiteLlm = FakeLiteLlm
    lite_llm.LiteLLMClient = FakeLiteLLMClient

    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.adk", google_adk)
    monkeypatch.setitem(sys.modules, "google.adk.models", google_adk_models)
    monkeypatch.setitem(sys.modules, "google.adk.models.lite_llm", lite_llm)

    calls = []
    litellm = ModuleType("litellm")

    def completion(**kwargs):
        calls.append(kwargs)
        return "completion"

    async def acompletion(**kwargs):
        calls.append(kwargs)
        return "acompletion"

    litellm.completion = completion
    litellm.acompletion = acompletion
    monkeypatch.setitem(sys.modules, "litellm", litellm)
    return calls


def test_lite_llm_client_allows_deployment_initiated_work(monkeypatch):
    """A Version-only callback leaves daemon resolution to the control plane."""
    calls = _fake_adk_modules(monkeypatch)
    monkeypatch.setattr(models, "aion_openai_config", lambda: FakeConfig())
    monkeypatch.setattr(models, "aion_model_api_key", lambda: "fresh-jwt")
    monkeypatch.setattr(
        "aion.api.callback_attribution.get_aion_runtime_context", lambda: None
    )

    client = aion_lite_llm("model-id").kwargs["llm_client"]

    client.completion(model="openai/model-id", messages=[], tools=None)
    asyncio.run(client.acompletion(model="openai/model-id", messages=[], tools=None))

    assert len(calls) == 2
    for call in calls:
        assert call["api_key"] == "fresh-jwt"
        assert call.get("extra_headers", {}) == {}


def test_lite_llm_does_not_derive_authority_from_environment_metadata(monkeypatch):
    """Routing metadata alone is not a callback authorization input."""
    from aion.core.exceptions import AionAuthenticationError
    calls = _fake_adk_modules(monkeypatch)
    monkeypatch.setattr(models, "aion_openai_config", lambda: FakeConfig())
    monkeypatch.setattr(models, "aion_model_api_key", lambda: "fresh-jwt")
    monkeypatch.setattr(
        model_service_client,
        "aion_principal_selector",
        lambda: "aion://agent/environment/env-id",
    )
    monkeypatch.setattr("aion.api.callback_attribution.get_aion_runtime_context",
                        lambda: AionRuntimeContext())

    client = aion_lite_llm("model-id").kwargs["llm_client"]

    with pytest.raises(AionAuthenticationError) as excinfo:
        asyncio.run(
            client.acompletion(model="openai/model-id", messages=[], tools=None)
        )

    assert "request-local attribution" in str(excinfo.value)
    assert calls == []


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("code", ["daemon_identity_required", "provider_unavailable"])
def test_litellm_stream_translates_only_configuration_errors(
    monkeypatch, asynchronous, partial, code,
):
    """Real LiteLLM iteration keeps typed errors, partial output and cleanup."""
    import openai

    monkeypatch.setattr(models, "aion_openai_config", lambda: FakeConfig())
    monkeypatch.setattr(models, "aion_model_api_key", lambda: "version-token")
    monkeypatch.setattr(models, "aion_model_request_headers", lambda _: {})
    consumed = []
    responses = []
    chunk = {"id": "chunk-1", "created": 0, "model": "test-model",
             "object": "chat.completion.chunk",
             "choices": [{"index": 0, "delta": {"content": "hello"}, "finish_reason": None}]}
    error = {"error": {"message": "An actionable error", "type": "configuration_error", "code": code}}

    def events():
        for event in ([chunk, error] if partial else [error]):
            consumed.append(event)
            yield ("data: " + json.dumps(event) + "\n\n").encode()

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            yield from events()

    class AsyncBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            for event in events():
                yield event

    def handle(request):
        response = httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  stream=AsyncBody() if asynchronous else Body())
        responses.append(response)
        return response

    client = models.aion_lite_llm("openai/test-model").llm_client
    kwargs = {"model": "openai/test-model", "messages": [{"role": "user", "content": "hello"}],
              "api_base": "https://api.example.test/v1", "stream": True}

    async def consume_async():
        async with openai.AsyncOpenAI(api_key="version-token", max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle))) as upstream:
            stream = await client.acompletion(**kwargs, client=upstream)
            assert not consumed
            if partial:
                first = await anext(stream)
                assert first.choices[0].delta.content == "hello"
                assert len(consumed) == 1
            await anext(stream)

    with pytest.raises((AionDaemonIdentityRequired, openai.APIError)) as caught:
        if asynchronous:
            asyncio.run(consume_async())
        else:
            with openai.OpenAI(api_key="version-token", max_retries=0,
                http_client=httpx.Client(transport=httpx.MockTransport(handle))) as upstream:
                stream = client.completion(**kwargs, client=upstream)
                assert not consumed
                if partial:
                    assert next(stream).choices[0].delta.content == "hello"
                    assert len(consumed) == 1
                next(stream)
    if code == "daemon_identity_required":
        assert isinstance(caught.value, AionDaemonIdentityRequired)
        assert caught.value.retryable is False
    else:
        assert not isinstance(caught.value, AionDaemonIdentityRequired)
        assert isinstance(caught.value, openai.APIError)
    assert len(responses) == 1
    assert responses[0].is_closed
    assert consumed[-1] == error

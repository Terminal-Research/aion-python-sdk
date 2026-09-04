import asyncio
from types import ModuleType
import sys

import pytest

from aion.adk.authoring.models import aion_lite_llm
import aion.adk.authoring.models as models
import aion.api.model_service_client as model_service_client
from aion.api.exceptions import AionModelPrincipalError


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


def test_lite_llm_client_refuses_a_call_with_no_principal(monkeypatch):
    """The ADK path is guarded by the same principal check as the httpx one.

    Both frameworks build their headers with aion_model_request_headers, so a
    deployment with no principal the model service accepts fails here too —
    before litellm is reached, rather than as an opaque server refusal.
    """
    calls = _fake_adk_modules(monkeypatch)
    monkeypatch.setattr(models, "aion_openai_config", lambda: FakeConfig())
    monkeypatch.setattr(models, "aion_model_api_key", lambda: "fresh-jwt")
    monkeypatch.setattr(
        model_service_client, "aion_principal_selector", lambda: None
    )

    client = aion_lite_llm("model-id").kwargs["llm_client"]

    with pytest.raises(AionModelPrincipalError):
        client.completion(model="openai/model-id", messages=[], tools=None)
    with pytest.raises(AionModelPrincipalError):
        asyncio.run(
            client.acompletion(model="openai/model-id", messages=[], tools=None)
        )

    assert calls == []


def test_lite_llm_client_refuses_an_environment_principal(monkeypatch):
    """An environment principal is not one the model service runs work for."""
    calls = _fake_adk_modules(monkeypatch)
    monkeypatch.setattr(models, "aion_openai_config", lambda: FakeConfig())
    monkeypatch.setattr(models, "aion_model_api_key", lambda: "fresh-jwt")
    monkeypatch.setattr(
        model_service_client,
        "aion_principal_selector",
        lambda: "aion://agent/environment/env-id",
    )

    client = aion_lite_llm("model-id").kwargs["llm_client"]

    with pytest.raises(AionModelPrincipalError) as excinfo:
        asyncio.run(
            client.acompletion(model="openai/model-id", messages=[], tools=None)
        )

    assert "Daemon Identity" in str(excinfo.value)
    assert calls == []

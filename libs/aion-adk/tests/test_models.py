import asyncio
from types import ModuleType
import sys

from aion.adk import aion_lite_llm
import aion.adk.models as models


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

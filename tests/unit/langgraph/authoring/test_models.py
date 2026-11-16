import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[4]
_MODELS_PATH = _REPO_ROOT / "src/aion/langgraph/authoring/models.py"
_SPEC = spec_from_file_location("aion_langgraph_models", _MODELS_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

aion_chat_model = _MODULE.aion_chat_model
aion_chat_openai = _MODULE.aion_chat_openai


class FakeConfig:
    def __init__(self):
        self.api_key = lambda: "jwt-token"

    def langchain_openai_kwargs(self):
        return {
            "base_url": "https://api.example.test/v1",
            "api_key": "aion-runtime-token",
            "http_client": "sync-client",
            "http_async_client": "async-client",
        }


def _fake_langchain(monkeypatch, captured):
    """Stand-ins for ``init_chat_model`` and ``ChatOpenAI`` that record how they were called."""
    langchain = ModuleType("langchain")
    chat_models = ModuleType("langchain.chat_models")
    langchain_openai = ModuleType("langchain_openai")

    def init_chat_model(**kwargs):
        captured.update(kwargs)
        return "configurable-model"

    class ChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    chat_models.init_chat_model = init_chat_model
    langchain_openai.ChatOpenAI = ChatOpenAI
    monkeypatch.setitem(sys.modules, "langchain", langchain)
    monkeypatch.setitem(sys.modules, "langchain.chat_models", chat_models)
    monkeypatch.setitem(sys.modules, "langchain_openai", langchain_openai)
    monkeypatch.setattr(_MODULE, "aion_openai_config", lambda: FakeConfig())
    return ChatOpenAI


def test_aion_chat_model_builds_a_chat_openai_that_sends_files_by_content(monkeypatch):
    captured = {}
    chat_openai = _fake_langchain(monkeypatch, captured)

    result = aion_chat_model(
        "model-id-from-control-plane",
        temperature=0,
    )

    assert isinstance(result, chat_openai) and type(result) is not chat_openai
    assert captured.pop("callbacks")[0].raise_error is True
    assert captured == {
        "model": "model-id-from-control-plane",
        "base_url": "https://api.example.test/v1",
        "api_key": "aion-runtime-token",
        "http_client": "sync-client",
        "http_async_client": "async-client",
        "temperature": 0,
    }


def test_a_configurable_aion_chat_model_is_langchains_own(monkeypatch):
    """``init_chat_model`` builds a configurable model's chat model per call, so it is returned as it is."""
    captured = {}
    _fake_langchain(monkeypatch, captured)

    result = aion_chat_model("model-id-from-control-plane", configurable_fields=("temperature",))

    assert result == "configurable-model"
    assert captured["model_provider"] == "openai"
    assert captured["configurable_fields"] == ("temperature",)


def test_aion_chat_openai_configures_chat_openai(monkeypatch):
    captured = {}
    langchain_openai = ModuleType("langchain_openai")

    class ChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    langchain_openai.ChatOpenAI = ChatOpenAI
    monkeypatch.setitem(sys.modules, "langchain_openai", langchain_openai)
    monkeypatch.setattr(_MODULE, "aion_openai_config", lambda: FakeConfig())

    result = aion_chat_openai("model-id-from-control-plane")

    assert isinstance(result, ChatOpenAI) and type(result) is not ChatOpenAI
    assert captured.pop("callbacks")[0].raise_error is True
    assert captured == {
        "model": "model-id-from-control-plane",
        "base_url": "https://api.example.test/v1",
        "api_key": "aion-runtime-token",
        "http_client": "sync-client",
        "http_async_client": "async-client",
    }


@pytest.mark.parametrize(
    "helper",
    [aion_chat_model, aion_chat_openai],
)
@pytest.mark.parametrize(
    "reserved_kwarg",
    [
        "api_key",
        "base_url",
        "default_headers",
        "http_async_client",
        "http_client",
    ],
)
def test_aion_chat_helpers_reject_reserved_connection_kwargs(
    helper,
    reserved_kwarg,
):
    with pytest.raises(ValueError, match=reserved_kwarg):
        helper("model-id-from-control-plane", **{reserved_kwarg: "custom"})

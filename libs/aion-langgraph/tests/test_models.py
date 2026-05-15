import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType


_MODELS_PATH = Path(__file__).parents[1] / "src/aion/langgraph/models.py"
_SPEC = spec_from_file_location("aion_langgraph_models", _MODELS_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

aion_chat_model = _MODULE.aion_chat_model
aion_chat_openai = _MODULE.aion_chat_openai


class FakeConfig:
    def __init__(self):
        self.api_key = lambda: "jwt-token"

    def openai_kwargs(self):
        return {
            "base_url": "https://api.example.test/v1",
            "api_key": self.api_key,
        }


def test_aion_chat_model_configures_langchain_init(monkeypatch):
    captured = {}
    langchain = ModuleType("langchain")
    chat_models = ModuleType("langchain.chat_models")

    def init_chat_model(**kwargs):
        captured.update(kwargs)
        return "chat-model"

    chat_models.init_chat_model = init_chat_model
    monkeypatch.setitem(sys.modules, "langchain", langchain)
    monkeypatch.setitem(sys.modules, "langchain.chat_models", chat_models)
    monkeypatch.setattr(_MODULE, "aion_openai_config", lambda: FakeConfig())

    result = aion_chat_model(
        "model-id-from-control-plane",
        temperature=0,
    )

    assert result == "chat-model"
    assert captured == {
        "model": "model-id-from-control-plane",
        "model_provider": "openai",
        "base_url": "https://api.example.test/v1",
        "api_key": captured["api_key"],
        "temperature": 0,
    }
    assert captured["api_key"]() == "jwt-token"


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

    assert isinstance(result, ChatOpenAI)
    assert captured == {
        "model": "model-id-from-control-plane",
        "base_url": "https://api.example.test/v1",
        "api_key": captured["api_key"],
    }
    assert captured["api_key"]() == "jwt-token"

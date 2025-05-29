import importlib


def test_public_api_exposes_all() -> None:
    module = importlib.import_module("aion_agent_api")
    assert isinstance(module.__all__, list)
    for name in module.__all__:
        assert hasattr(module, name)


def test_star_import() -> None:
    namespace: dict[str, object] = {}
    exec("from aion_agent_api import *", namespace)
    # Ensure a known symbol is imported
    assert "A2AServer" in namespace

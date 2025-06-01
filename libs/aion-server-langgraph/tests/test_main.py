import importlib
from unittest import mock
import pytest

pytest.importorskip("pydantic")
pytest.importorskip("a2a")


def test_main_runs_migrations(monkeypatch):
    monkeypatch.setenv("POSTGRES_URL", "postgresql://example")
    monkeypatch.setenv("OPENROUTER_API_KEY", "key")
    module = importlib.import_module("aion.server.langgraph.__main__")

    monkeypatch.setattr(module, "test_connection", lambda url: True)
    called = {}
    monkeypatch.setattr(module, "upgrade_to_head", lambda: called.setdefault("ran", True))
    monkeypatch.setattr(module, "initialize_graphs", lambda: module.GRAPHS.setdefault("g", object()))
    monkeypatch.setattr(module, "PostgresTaskStore", lambda: object())
    monkeypatch.setattr(module, "LanggraphAgentExecutor", lambda g: object())
    monkeypatch.setattr(module, "InMemoryTaskStore", lambda: object())
    monkeypatch.setattr(module, "A2AStarletteApplication", lambda agent_card, http_handler: mock.Mock(build=lambda: "app"))
    monkeypatch.setattr(module, "httpx", mock.Mock(AsyncClient=lambda: mock.Mock()))
    monkeypatch.setattr(module, "uvicorn", mock.Mock(run=lambda app, host, port: None))

    module.main.callback(host="localhost", port=10000)
    assert called.get("ran")

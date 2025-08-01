import importlib
import logging
from unittest import mock
import pytest

pytest.importorskip("pydantic")
pytest.importorskip("a2a")


def test_main_runs_migrations(monkeypatch, caplog):
    monkeypatch.setenv("POSTGRES_URL", "postgresql://example")
    monkeypatch.setenv("OPENROUTER_API_KEY", "key")
    module = importlib.import_module("aion.server.langgraph.__main__")

    monkeypatch.setattr(module, "test_connection", lambda url: True)
    called = {}
    monkeypatch.setattr(module, "upgrade_to_head", lambda: called.setdefault("ran", True))
    # Mock graph_manager and its initialize_graphs method
    mock_graph_manager = mock.Mock()
    mock_graph_manager.initialize_graphs = mock.Mock()
    monkeypatch.setattr(module, "graph_manager", mock_graph_manager)

    monkeypatch.setattr(module, "PostgresTaskStore", lambda: object())
    monkeypatch.setattr(module, "LanggraphAgentExecutor", lambda g: object())
    monkeypatch.setattr(module, "InMemoryTaskStore", lambda: object())
    monkeypatch.setattr(module, "A2AStarletteApplication", lambda agent_card, http_handler: mock.Mock(build=lambda: "app"))
    monkeypatch.setattr(module, "httpx", mock.Mock(AsyncClient=lambda: mock.Mock()))
    monkeypatch.setattr(module, "uvicorn", mock.Mock(run=lambda app, host, port: None))

    with caplog.at_level(logging.DEBUG):
        module.main.callback(host="localhost", port=10000)
    assert called.get("ran")
    assert "Running database migrations" in caplog.text
    assert "Database migrations completed" in caplog.text

import asyncio
from unittest import mock
import logging

import pytest

pytest.importorskip("pydantic")

from aion.server.db import test_connection
from aion.server.langgraph.a2a.tasks import PostgresTaskStore


class DummyCursor:
    def execute(self, *args, **kwargs):
        pass

    def close(self):
        pass


class DummyConnection:
    def __init__(self):
        self.closed = False

    def cursor(self):
        return DummyCursor()

    def commit(self):
        pass

    def close(self):
        self.closed = True


def test_test_connection_success(monkeypatch, caplog):
    conn = DummyConnection()

    def fake_connect(url):
        return conn

    monkeypatch.setenv("POSTGRES_URL", "postgresql://example")
    monkeypatch.setattr("psycopg.connect", fake_connect)

    with caplog.at_level(logging.INFO):
        assert test_connection("postgresql://example")
    assert conn.closed
    assert "Successfully connected to Postgres" in caplog.text


def test_test_connection_failure(monkeypatch, caplog):
    def fake_connect(url):
        raise RuntimeError("could not connect")

    monkeypatch.setenv("POSTGRES_URL", "postgresql://example")
    monkeypatch.setattr("psycopg.connect", fake_connect)

    with caplog.at_level(logging.ERROR):
        assert not test_connection("postgresql://example")
    assert "Could not connect to Postgres" in caplog.text


def test_postgres_task_store_saves(monkeypatch):
    task = mock.Mock()
    task.contextId = "ctx"
    task.model_dump_json.return_value = "{}"

    recorded = {}

    class Cursor:
        def execute(self, sql, params):
            recorded["sql"] = sql
            recorded["params"] = params

        def close(self):
            pass

    class Conn(DummyConnection):
        def cursor(self):
            return Cursor()

    def fake_connect(url):
        recorded["dsn"] = url
        return Conn()

    monkeypatch.setenv("POSTGRES_URL", "postgresql://example")
    monkeypatch.setattr("psycopg.connect", fake_connect)

    store = PostgresTaskStore()
    asyncio.run(store.save_task(task))

    assert recorded["dsn"] == "postgresql://example"
    assert "INSERT INTO tasks" in recorded["sql"]



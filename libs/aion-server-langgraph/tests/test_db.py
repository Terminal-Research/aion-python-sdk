import builtins
from unittest import mock

import asyncio
from unittest import mock

import pytest

pytest.importorskip("pydantic")

from aion.server.langgraph.__main__ import check_postgres_connection
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


def test_check_postgres_connection_success(monkeypatch, caplog):
    conn = DummyConnection()

    def fake_connect(url):
        return conn

    monkeypatch.setenv("POSTGRES_URL", "postgresql://example")
    monkeypatch.setattr("psycopg.connect", fake_connect)

    check_postgres_connection()
    assert conn.closed


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



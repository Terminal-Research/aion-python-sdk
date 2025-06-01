import builtins
from unittest import mock

import asyncio
from unittest import mock

import pytest

pytest.importorskip("pydantic")

from aion.server.langgraph.__main__ import check_postgres_connection
from aion.server.langgraph.a2a.tasks import PostgresTaskStore


class DummyCursor:
    def __init__(self, row=None, recorder=None):
        self.row = row
        self.recorder = recorder if recorder is not None else {}

    def execute(self, sql, params):
        if isinstance(self.recorder, list):
            self.recorder.append(sql)
        else:
            self.recorder["sql"] = sql
            self.recorder["params"] = params

    def fetchone(self):
        return (self.row,) if self.row is not None else None

    def close(self):
        pass


class DummyConnection:
    def __init__(self, cursor=None):
        self.closed = False
        self._cursor = cursor or DummyCursor()

    def cursor(self):
        return self._cursor

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


def test_postgres_task_store_methods(monkeypatch):
    task = mock.Mock()
    task.id = "00000000-0000-0000-0000-000000000000"
    task.contextId = "ctx"
    task.model_dump_json.return_value = "{}"

    sql_log: list[str] = []
    cursors = [
        DummyCursor(recorder=sql_log),
        DummyCursor(row="{}", recorder=sql_log),
        DummyCursor(recorder=sql_log),
    ]

    conns = [DummyConnection(c) for c in cursors]

    def fake_connect(url):
        assert url == "postgresql://example"
        return conns.pop(0)

    monkeypatch.setenv("POSTGRES_URL", "postgresql://example")
    monkeypatch.setattr("psycopg.connect", fake_connect)

    store = PostgresTaskStore()
    asyncio.run(store.save(task))
    result = asyncio.run(store.get(task.id))
    asyncio.run(store.delete(task.id))

    assert any("INSERT INTO tasks" in s for s in sql_log)
    assert any("SELECT task FROM tasks" in s for s in sql_log)
    assert any("DELETE FROM tasks" in s for s in sql_log)
    assert result is not None



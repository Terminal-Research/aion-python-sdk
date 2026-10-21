"""Tests for the request line the tracing middleware writes.

Every request gets one INFO line with its status, except a healthy answer to
the platform's probe, which arrives every 30 s and is not an event.
"""

import logging

import pytest

from aion.server.core.middlewares.tracing import TracingMiddleware


class FakeUrl:
    def __init__(self, path: str):
        self.path = path


class FakeRequest:
    def __init__(self, method: str, path: str):
        self.method = method
        self.url = FakeUrl(path)


class FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


@pytest.fixture
def middleware():
    return TracingMiddleware(app=object())


def log_records(middleware, caplog, method: str, path: str, status: int):
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="aion.server.core.middlewares.tracing"):
        middleware._log_request_response(FakeRequest(method, path), FakeResponse(status), None)
    return caplog.records


def test_a_healthy_probe_leaves_no_line(middleware, caplog):
    assert log_records(middleware, caplog, "GET", "/health/", 200) == []


def test_a_failed_probe_is_reported(middleware, caplog):
    records = log_records(middleware, caplog, "GET", "/health/", 503)

    assert [r.levelno for r in records] == [logging.INFO]
    assert records[0].getMessage() == "GET /health/ | 503"


def test_any_other_request_gets_its_line(middleware, caplog):
    records = log_records(middleware, caplog, "GET", "/.well-known/agent-card.json", 200)

    assert [r.getMessage() for r in records] == ["GET /.well-known/agent-card.json | 200"]

"""Tests for the proxy's request logging level.

Every request the proxy forwards is logged a second time by the agent that
serves it, so the proxy's copy earns info level only when it carries something
the agent's line cannot.
"""

import logging

import pytest

from aion.proxy.middlewares.logging import ProxyLoggingMiddleware


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
    return ProxyLoggingMiddleware(app=object())


def log_records(middleware, caplog, status: int):
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="aion.proxy.middlewares.logging"):
        middleware._log_request_response(
            FakeRequest("POST", "/agents/command-agent/"), FakeResponse(status))
    return caplog.records


def test_a_forwarded_request_is_not_announced_twice(middleware, caplog):
    """The agent logs the same request, so the proxy's hop stays at debug."""
    records = log_records(middleware, caplog, 200)

    assert [r.levelno for r in records] == [logging.DEBUG]


def test_a_redirect_stays_quiet_too(middleware, caplog):
    """A missing trailing slash redirects on every POST; it is not an incident."""
    records = log_records(middleware, caplog, 307)

    assert [r.levelno for r in records] == [logging.DEBUG]


@pytest.mark.parametrize("status", [404, 502, 504])
def test_a_request_that_never_reached_an_agent_is_reported(middleware, caplog, status):
    """No agent logs these, so the proxy's line is the only record of them."""
    records = log_records(middleware, caplog, status)

    assert [r.levelno for r in records] == [logging.WARNING]
    assert str(status) in records[0].getMessage()

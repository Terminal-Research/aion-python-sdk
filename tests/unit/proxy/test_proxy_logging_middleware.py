"""Tests for the proxy's request logging level.

A request the agent answered, or a forwarding failure the handler logged, is
already in the log, so the proxy's line stays at debug; only a failure the
proxy produced on its own is reported.
"""

import logging

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aion.proxy.handlers import RequestHandler
from aion.proxy.middlewares.logging import ProxyLoggingMiddleware
from aion.proxy.routes import ProxyRouter


class FakeUrl:
    def __init__(self, path: str):
        self.path = path


class FakeState:
    pass


class FakeRequest:
    def __init__(self, method: str, path: str, logged_elsewhere: bool = False):
        self.method = method
        self.url = FakeUrl(path)
        self.state = FakeState()
        if logged_elsewhere:
            self.state.logged_elsewhere = True


class FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


@pytest.fixture
def middleware():
    return ProxyLoggingMiddleware(app=object())


def log_records(middleware, caplog, status: int, logged_elsewhere: bool = False):
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="aion.proxy.middlewares.logging"):
        middleware._log_request_response(
            FakeRequest("POST", "/agents/command-agent/", logged_elsewhere), FakeResponse(status))
    return caplog.records


def test_a_forwarded_request_is_not_announced_twice(middleware, caplog):
    """The agent logs the same request, so the proxy's hop stays at debug."""
    records = log_records(middleware, caplog, 200)

    assert [r.levelno for r in records] == [logging.DEBUG]


def test_a_redirect_stays_quiet_too(middleware, caplog):
    """A missing trailing slash redirects on every POST; it is not an incident."""
    records = log_records(middleware, caplog, 307)

    assert [r.levelno for r in records] == [logging.DEBUG]


@pytest.mark.parametrize("status", [401, 500])
def test_an_agents_failure_is_relayed_without_a_second_line(middleware, caplog, status):
    """The agent logged its own refusal or error; the proxy only passed it on."""
    records = log_records(middleware, caplog, status, logged_elsewhere=True)

    assert [r.levelno for r in records] == [logging.DEBUG]


@pytest.mark.parametrize("status", [404, 502, 504])
def test_a_request_that_never_reached_an_agent_is_reported(middleware, caplog, status):
    """No agent logs these, so the proxy's line is the only record of them."""
    records = log_records(middleware, caplog, status)

    assert [r.levelno for r in records] == [logging.WARNING]
    assert str(status) in records[0].getMessage()


class _ProxyServer:
    def __init__(self, app):
        self.app = app
        self.agent_urls = {"command-agent": "http://agent.test"}


def _proxy_client(transport_handler):
    """The proxy's routes, forwarder and logging middleware, over a mocked agent."""
    app = FastAPI()
    app.add_middleware(ProxyLoggingMiddleware)
    handler = RequestHandler(
        {"command-agent": "http://agent.test"},
        httpx.AsyncClient(transport=httpx.MockTransport(transport_handler)),
    )
    ProxyRouter(agent_proxy_server=_ProxyServer(app), request_handler=handler).register_routes()
    return TestClient(app)


def _proxy_records(caplog):
    return [r for r in caplog.records if r.name.startswith("aion.proxy")]


def test_an_agents_refusal_passes_through_the_proxy_without_a_line(caplog):
    with caplog.at_level(logging.INFO, logger="aion.proxy"):
        response = _proxy_client(lambda request: httpx.Response(401, stream=httpx.ByteStream(b"refused"))).post("/agents/command-agent", json={})

    assert response.status_code == 401
    assert _proxy_records(caplog) == []


def test_an_unreachable_agent_is_one_error_with_its_cause(caplog):
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    with caplog.at_level(logging.INFO, logger="aion.proxy"):
        response = _proxy_client(refuse).post("/agents/command-agent", json={})

    assert response.status_code == 503
    assert [(r.levelno, r.getMessage().startswith("Failed to connect")) for r in _proxy_records(caplog)] == [
        (logging.ERROR, True)
    ]


def test_an_unknown_agent_is_one_warning_from_the_proxy(caplog):
    with caplog.at_level(logging.INFO, logger="aion.proxy"):
        response = _proxy_client(lambda request: httpx.Response(200)).post("/agents/nobody", json={})

    assert response.status_code == 404
    assert [r.levelno for r in _proxy_records(caplog)] == [logging.WARNING]

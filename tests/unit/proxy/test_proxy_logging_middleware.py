"""Tests for the proxy's request logging level.

A request the agent answered, or a forwarding failure the handler logged, is
already in the log, so the proxy's line stays at debug; only a failure the
proxy produced on its own is reported. A healthy probe leaves no line.
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
        self.headers = {}
        self.client = None
        if logged_elsewhere:
            self.state.logged_elsewhere = True


class FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


@pytest.fixture
def middleware():
    return ProxyLoggingMiddleware(app=object())


def log_records(middleware, caplog, status: int, logged_elsewhere: bool = False,
                method: str = "POST", path: str = "/agents/command-agent/"):
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="aion.proxy.middlewares.logging"):
        middleware._log_request_response(
            FakeRequest(method, path, logged_elsewhere), FakeResponse(status))
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


@pytest.mark.parametrize("path", ["/health/", "/health/system/"])
def test_a_healthy_probe_leaves_no_line(middleware, caplog, path):
    """The platform probes every 30 s; a healthy answer is not an event."""
    records = log_records(middleware, caplog, 200, method="GET", path=path)

    assert records == []


def test_a_failed_probe_is_reported(middleware, caplog):
    """Only a healthy probe is silent; a failing one is the proxy's own failure."""
    records = log_records(middleware, caplog, 503, method="GET", path="/health/system/")

    assert [r.levelno for r in records] == [logging.WARNING]
    assert "/health/system/" in records[0].getMessage()


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


@pytest.mark.parametrize("user_agent", ["ELB-HealthChecker/2.0", "kube-probe/1.35"])
def test_root_probe_diagnostics_survive_logstash_formatting(caplog, user_agent):
    import json

    from aion.server.logging.handlers.logstash import AionLogstashFormatter

    app = FastAPI()
    app.add_middleware(ProxyLoggingMiddleware)
    with TestClient(app, client=("172.31.1.2", 43210)) as client:
        with caplog.at_level(logging.WARNING, logger="aion.proxy"):
            response = client.get("/?token=query-secret", headers={
                "user-agent": user_agent,
                "host": "172.31.2.3:8080",
                "x-forwarded-for": "192.0.2.1, 192.0.2.2",
                "x-real-ip": "192.0.2.1",
                "authorization": "Bearer auth-secret",
                "cookie": "session=cookie-secret",
                "x-private": "private-secret",
            })
    assert response.status_code == 404
    record, = _proxy_records(caplog)
    payload = AionLogstashFormatter(client_id="test", host_name="test").format(record)
    details = json.loads(payload)["proxy_request"]
    assert details == {
        "method": "GET", "path": "/", "status_code": 404,
        "client_host": "172.31.1.2", "client_port": 43210,
        "user_agent": user_agent, "host": "172.31.2.3:8080",
        "forwarded_for": "192.0.2.1, 192.0.2.2", "real_ip": "192.0.2.1",
    }
    assert user_agent in record.getMessage()
    assert "secret" not in payload


def test_diagnostics_bound_and_sanitize_untrusted_values(middleware, caplog):
    from starlette.requests import Request

    request = Request({
        "type": "http", "method": "GET", "path": "/\nforged",
        "headers": [(b"user-agent", b"probe\r\n\x1b[31m" + b"x" * 1000)],
        "query_string": b"secret=value", "client": None,
    })
    with caplog.at_level(logging.WARNING, logger="aion.proxy"):
        middleware._log_request_response(request, FakeResponse(404))
    record, = _proxy_records(caplog)
    assert len(record.proxy_request["user_agent"]) == 512
    assert "client_host" not in record.proxy_request
    assert all(c not in record.getMessage() for c in ("\n", "\r", "\x1b"))
    assert "secret" not in record.getMessage()

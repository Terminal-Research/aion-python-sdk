"""Runtime proof belongs to the process that produces the response."""

from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

from aion.core.deployment import RUNTIME_ATTEMPT_HEADER
from aion.proxy.handlers import RequestHandler
from aion.proxy.routes import ProxyRouter
from aion.proxy.server import AionAgentProxyServer
from aion.server.settings import app_settings


VERSION = "00000000-0000-0000-0000-000000000001"
ATTEMPT = "00000000-0000-0000-0000-000000000002"
OLDER_ATTEMPT = "00000000-0000-0000-0000-000000000003"


def proxy_client(monkeypatch, attempt, upstream_attempt=None):
    """Wire the real proxy to a controlled upstream, without opening sockets."""
    monkeypatch.setattr(app_settings, "version_id", VERSION)
    monkeypatch.setattr(
        app_settings, "runtime_attempt_id", UUID(attempt) if attempt else None)
    proxy = AionAgentProxyServer({"agent": "http://agent.test"})

    def respond(request):
        headers = ({RUNTIME_ATTEMPT_HEADER: upstream_attempt}
                   if upstream_attempt else {})
        body = b'{"status":"healthy"}'
        if request.url.path.endswith("/docs"):
            headers["content-type"] = "text/html"
            body = b'<script>url: "/openapi.json"</script>'
        elif request.url.path.endswith("/openapi.json"):
            headers["content-type"] = "application/json"
            body = b'{"openapi":"3.1.0"}'
        return httpx.Response(
            200, headers=headers, stream=httpx.ByteStream(body))

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    ProxyRouter(proxy, RequestHandler(proxy.agent_urls, upstream)).register_routes()
    return TestClient(proxy.app)


@pytest.mark.parametrize("attempt", [None, ATTEMPT])
def test_owned_responses_keep_the_startup_identity(monkeypatch, attempt):
    """Settings changes and caller headers cannot relabel a serving runtime."""
    client = proxy_client(monkeypatch, attempt)
    monkeypatch.setattr(app_settings, "version_id", OLDER_ATTEMPT)
    monkeypatch.setattr(app_settings, "runtime_attempt_id", UUID(OLDER_ATTEMPT))

    for path in ("/.well-known/manifest.json", "/health/system/", "/health/"):
        response = client.get(path, headers={RUNTIME_ATTEMPT_HEADER: "caller-input"})
        assert response.status_code == 200
        assert response.headers.get(RUNTIME_ATTEMPT_HEADER) == attempt
        if path == "/health/":
            assert response.json() == {"status": "healthy"}
        elif attempt:
            assert response.json()["runtime"] == {
                "versionId": VERSION, "runtimeAttemptId": ATTEMPT}
        else:
            assert "runtime" not in response.json()


@pytest.mark.parametrize("upstream_attempt", [None, OLDER_ATTEMPT])
def test_proxy_never_supplies_or_replaces_agent_proof(monkeypatch, upstream_attempt):
    """A current proxy cannot make an older or unidentified agent look current."""
    client = proxy_client(monkeypatch, ATTEMPT, upstream_attempt)
    for path in (".well-known/agent-card.json", ".well-known/configuration.json",
                 "docs", "openapi.json"):
        response = client.get(f"/agents/agent/{path}")
        assert response.status_code == 200
        assert response.headers.get(RUNTIME_ATTEMPT_HEADER) == upstream_attempt
        if path == "docs":
            assert 'url: "/agents/agent/openapi.json"' in response.text
        elif path == "openapi.json":
            assert response.json()["servers"][0]["url"] == "/agents/agent"
        else:
            assert response.content == b'{"status":"healthy"}'

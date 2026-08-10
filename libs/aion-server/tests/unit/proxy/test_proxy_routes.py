"""Tests for how the proxy addresses an agent.

The proxy is the central entry point for talking to an agent, so the address
callers are given has to work as handed to them - including for a client that
will not follow a redirect on POST.
"""

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from aion.proxy.routes import ProxyRouter


class RecordingRequestHandler:
    """Stands in for the forwarder, reporting what it was asked to forward."""

    def __init__(self):
        self.calls = []

    async def forward_request(self, agent_id: str, path: str, request):
        self.calls.append((agent_id, path))
        return JSONResponse({"agent_id": agent_id, "path": path})


class FakeProxyServer:
    def __init__(self, app: FastAPI):
        self.app = app
        self.agent_urls = {"command-agent": "http://127.0.0.1:8001"}


@pytest.fixture
def handler():
    return RecordingRequestHandler()


@pytest.fixture
def client(handler):
    app = FastAPI()
    ProxyRouter(agent_proxy_server=FakeProxyServer(app), request_handler=handler).register_routes()
    # Redirects are not followed: a caller that does follow them cannot tell a
    # direct hit from a round trip, which is the very thing under test.
    return TestClient(app, follow_redirects=False)


def test_rpc_root_answers_without_a_redirect(client, handler):
    """A POST to /agents/{id} used to cost a 307 and a second send of the body."""
    response = client.post("/agents/command-agent", json={"jsonrpc": "2.0"})

    assert response.status_code == 200
    assert handler.calls == [("command-agent", "")]


def test_the_trailing_slash_form_is_unchanged(client, handler):
    """The address the CLI prints must keep working exactly as before."""
    response = client.post("/agents/command-agent/", json={"jsonrpc": "2.0"})

    assert response.status_code == 200
    assert handler.calls == [("command-agent", "")]


def test_a_deeper_path_still_reaches_the_agent(client, handler):
    """Both forms share one forwarder, so the path must survive the split."""
    response = client.get("/agents/command-agent/.well-known/agent-card.json")

    assert response.status_code == 200
    assert handler.calls == [("command-agent", ".well-known/agent-card.json")]


@pytest.mark.parametrize("method", ["get", "put", "delete", "patch"])
def test_every_forwarded_method_reaches_the_root(client, handler, method):
    """The root form carries the same method set as the path form."""
    response = getattr(client, method)("/agents/command-agent")

    assert response.status_code == 200
    assert handler.calls == [("command-agent", "")]


class TestAdvertisedAddressesAreRouted:
    """Every address we hand out must be served as handed out.

    A route the proxy does not declare is not refused - Starlette redirects to
    the trailing-slash form instead - so an address that drifted from the routes
    keeps working while costing every caller an extra round trip, and failing
    outright for one that will not follow a redirect on POST.
    """

    @pytest.mark.parametrize("path", ["", "docs", "openapi.json", ".well-known/agent-card.json"])
    def test_a_built_address_is_reached_without_a_redirect(self, client, path):
        from aion.proxy.constants import build_agent_path

        response = client.get(build_agent_path("command-agent", path))

        assert response.status_code == 200, f"{build_agent_path('command-agent', path)} redirected"

    def test_the_manifest_endpoints_are_reached_without_a_redirect(self, client, handler):
        """The manifest is how a caller discovers where to send its requests."""
        endpoints = client.get("/.well-known/manifest.json").json()["endpoints"]

        assert endpoints, "the manifest must advertise at least one endpoint"
        for agent_id, endpoint in endpoints.items():
            response = client.post(endpoint, json={"jsonrpc": "2.0"})

            assert response.status_code == 200, f"{endpoint} redirected"
            assert handler.calls[-1] == (agent_id, "")
